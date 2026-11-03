"""Reviewed local evaluation data; foreign claims never become executable grades."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import tempfile

from agent_harness_lab.private_journal import encoded, write_bytes, sync_directory
from .integrity import contained, read_regular, unique_object, invalid_constant, tree
from .regressions import RegressionProposal, fields, references, sha256, text
from .task_bank import identifier

PROFILE = 'external-evaluation-data-v1'
FORMATS = ('promptfoo-cases-json-v1', 'promptfoo-results-jsonl-v1', 'openai-evals-match-jsonl-v1')
MAX_BYTES = 1024*1024
SENSITIVE = {'api_key','apikey','authorization','password','access_token','headers','env'}


def _hash(raw): return hashlib.sha256(raw).hexdigest()


def _bounded(value, depth=0):
    if depth > 32: raise ValueError('interchange nesting bound exceeded')
    if isinstance(value,dict):
        for key, item in value.items():
            if key.casefold() in SENSITIVE: raise ValueError('sensitive configuration is unsupported')
            _bounded(key,depth+1); _bounded(item,depth+1)
    elif isinstance(value,list):
        if len(value)>1000: raise ValueError('interchange collection bound exceeded')
        for item in value: _bounded(item,depth+1)
    elif isinstance(value,str):
        if len(value.encode('utf-8'))>65536 or '\0' in value: raise ValueError('interchange text bound exceeded')
    elif type(value) in (int,float):
        if not math.isfinite(value): raise ValueError('finite interchange number required')
    elif value is not None and type(value) is not bool: raise ValueError('unsupported interchange value')


def _json(raw):
    try:
        value=json.loads(raw,object_pairs_hook=unique_object,parse_constant=invalid_constant)
        _bounded(value)
        return value
    except (RecursionError,UnicodeError,OverflowError):
        raise ValueError('invalid bounded interchange JSON') from None


def _optional(value):
    if value is not None: text(value)


def _literal(value):
    _bounded(value)
    if isinstance(value,str) and (value.startswith(('file://','http://','https://')) or '{{' in value or '{%' in value):
        raise ValueError('external references and template evaluation are unsupported')
    if isinstance(value,(dict,list)):
        for item in (value.values() if isinstance(value,dict) else value): _literal(item)


def _assertion(value):
    if not isinstance(value,dict) or not {'type','value'}<=set(value) or set(value)-{'type','value','weight','metric'}:
        raise ValueError('unsupported assertion configuration')
    if value['type'] not in ('equals','contains','llm-rubric') or not isinstance(value['value'],str):
        raise ValueError('unsupported assertion type/reference')
    _literal(value['value'])
    if 'weight' in value and (type(value['weight']) not in (int,float) or value['weight']<0):
        raise ValueError('invalid assertion weight')
    if 'metric' in value: text(value['metric'])


def _case(value):
    if not isinstance(value,dict) or not {'vars','assert'}<=set(value) or set(value)-{'vars','assert','description','threshold'}:
        raise ValueError('only inline data-only Promptfoo cases are supported')
    if not isinstance(value['vars'],dict) or not isinstance(value['assert'],list) or not 0<len(value['assert'])<=32:
        raise ValueError('bounded inline variables/assertions required')
    _literal(value['vars'])
    for assertion in value['assert']: _assertion(assertion)
    if 'threshold' in value and type(value['threshold']) not in (int,float): raise ValueError('numeric threshold required')
    if 'description' in value and not isinstance(value['description'],str): raise ValueError('text description required')


def _grade(value, *, component=False):
    if value is None: return
    if not isinstance(value,dict) or not {'pass','score'}<=set(value) or set(value)-{'pass','score','reason','componentResults','assertion'}:
        raise ValueError('unsupported foreign grading result')
    if type(value['pass']) is not bool or type(value['score']) not in (int,float): raise ValueError('typed foreign pass/score required')
    if 'reason' in value and not isinstance(value['reason'],str): raise ValueError('text grading reason required')
    if 'assertion' in value: _assertion(value['assertion'])
    if 'componentResults' in value:
        if component or not isinstance(value['componentResults'],list) or len(value['componentResults'])>32:
            raise ValueError('unsupported nested component results')
        for child in value['componentResults']:
            if child is None: raise ValueError('missing component result')
            _grade(child,component=True)


def _result(value):
    if not isinstance(value,dict) or not {'testIdx','promptIdx','success','score'}<=set(value) or set(value)-{
            'testIdx','promptIdx','success','score','response','gradingResult','error'}:
        raise ValueError('unsupported Promptfoo JSONL result fields')
    if any(type(value[k]) is not int or value[k]<0 for k in ('testIdx','promptIdx')):
        raise ValueError('nonnegative result indices required')
    if type(value['success']) is not bool or type(value['score']) not in (int,float): raise ValueError('typed foreign success/score required')
    if 'response' in value:
        fields(value['response'],'output')
        if not isinstance(value['response']['output'],str): raise ValueError('text result output required')
    if 'gradingResult' in value: _grade(value['gradingResult'])
    if 'error' in value and not isinstance(value['error'],str): raise ValueError('text foreign error required')


def _sample(value):
    fields(value,'input ideal')
    if isinstance(value['input'],list):
        if not 0<len(value['input'])<=32: raise ValueError('bounded chat sample required')
        for message in value['input']:
            fields(message,'role content')
            if message['role'] not in ('system','user','assistant') or not isinstance(message['content'],str):
                raise ValueError('unsupported chat sample')
    elif not isinstance(value['input'],str): raise ValueError('text or chat input required')
    ideals=value['ideal'] if isinstance(value['ideal'],list) else [value['ideal']]
    if not 0<len(ideals)<=32 or any(not isinstance(v,str) for v in ideals): raise ValueError('bounded ideal references required')


def _mapping(value):
    fields(value,'schema_version format source review runtime score_descriptor items registry_sha256')
    if type(value['schema_version']) is not int or value['schema_version']!=1 or value['format'] not in FORMATS:
        raise ValueError('unsupported interchange mapping profile')
    source=value['source']; fields(source,'repository revision format_version dataset_id source_kind permission dataset_license sha256')
    for key in ('repository','revision','format_version','dataset_id'): text(source[key])
    if source['source_kind'] not in ('synthetic','public','private') or source['permission']!='approved':
        raise ValueError('explicitly approved source permission required')
    fields(source['dataset_license'],'id ref')
    for item in source['dataset_license'].values():
        text(item)
        if item.casefold() in ('unknown','unverified','unspecified'): raise ValueError('applicable dataset license evidence required')
    sha256(source['sha256'])
    review=value['review']; fields(review,'identity source evidence_refs'); identifier(review['identity']); references(review['evidence_refs'])
    if review['source']!='operator_attestation' or not review['evidence_refs']: raise ValueError('explicit operator review evidence required')
    fields(value['runtime'],'provider model_revision configuration_sha256')
    for key in ('provider','model_revision'): _optional(value['runtime'][key])
    if value['runtime']['configuration_sha256'] is not None: sha256(value['runtime']['configuration_sha256'])
    descriptor=value['score_descriptor']
    if descriptor is not None:
        fields(descriptor,'metric kind scale aggregation judge_ref'); text(descriptor['metric']); _optional(descriptor['judge_ref'])
        if descriptor['kind'] not in ('deterministic','model_judged','aggregate','unknown') or descriptor['aggregation'] not in ('assertion','case','dataset'):
            raise ValueError('explicit score kind/aggregation required')
        fields(descriptor['scale'],'minimum maximum unit'); text(descriptor['scale']['unit'])
        for key in ('minimum','maximum'):
            if descriptor['scale'][key] is not None and type(descriptor['scale'][key]) not in (int,float): raise ValueError('numeric or unknown score bounds required')
        low,high=descriptor['scale']['minimum'],descriptor['scale']['maximum']
        if low is not None and high is not None and low>high: raise ValueError('inverted score scale')
    if value['format']==FORMATS[1] and descriptor is None: raise ValueError('foreign result score provenance required')
    if value['registry_sha256'] is not None: sha256(value['registry_sha256'])
    items=value['items']
    if not isinstance(items,list) or not 0<len(items)<=1000: raise ValueError('bounded explicit item mappings required')
    for item in items:
        fields(item,'record source_item_id task_id root_id family source_split partition exposure expected_boundary')
        if type(item['record']) is not int or item['record']<1: raise ValueError('one-based explicit record locator required')
        for key in ('source_item_id','task_id','root_id','family'): identifier(item[key]); text(item[key])
        text(item['source_split']); text(item['expected_boundary'])
        if item['partition'] not in ('construction','development') or item['exposure']!='development':
            raise ValueError('imports remain exposed construction/development data')
    for key in ('record','source_item_id'):
        if len({item[key] for item in items})!=len(items): raise ValueError('duplicate or ambiguous item mapping')
    groups={}; tasks={}
    for item in items:
        lineage=(item['root_id'],item['family'])
        if groups.setdefault(item['root_id'],item['family'])!=item['family'] or tasks.setdefault(item['task_id'],lineage)!=lineage:
            raise ValueError('inconsistent declared task/root/family mapping')
    if value['format']!=FORMATS[1] and len(tasks)!=len(items): raise ValueError('duplicate case task mapping')
    return value


def _proposal(item, mapping, item_sha):
    ref=dict(ref='external-evaluation:'+item_sha,sha256=item_sha)
    empty=dict(harness_sha256=None,config_sha256=None,memory_sha256=None,evidence_refs=[])
    value=dict(schema_version=1,proposal_id=item_sha,root_id=item['root_id'],family=item['family'],
        mechanism='External evaluation data awaiting AHL reproduction and oracle review',
        source=dict(kind=mapping['source']['source_kind'],permission='approved',evidence_refs=[ref],trace_refs=[]),
        before=empty,after=empty,task_start=dict(ref=None,sha256=None),
        environment=dict(image_sha256=None,runtime_ref=None,requirements=['A separately declared AHL offline reproduction environment']),
        expected_boundary=item['expected_boundary'],reproduction=dict(disposition='insufficient_evidence',
            original_outcome='unknown',replay_outcome='unknown',run_refs=[],boundary_refs=[],
            reason='Foreign reported claims do not establish an AHL task failure or executable oracle.'))
    return RegressionProposal.parse(value).value


def _capture(mapping_path, source_path, registry_path):
    from .decision_receipts import engine
    evaluator=engine(); engine_sha=_hash(encoded(evaluator))
    raw_mapping=read_regular(mapping_path,MAX_BYTES); mapping=_mapping(_json(raw_mapping))
    raw_source=read_regular(source_path,MAX_BYTES)
    if _hash(raw_source)!=mapping['source']['sha256']: raise ValueError('external source digest changed')
    registry=None; raw_registry=None
    if mapping['format']==FORMATS[2]:
        if registry_path is None or mapping['registry_sha256'] is None: raise ValueError('explicit frozen registry metadata required')
        raw_registry=read_regular(registry_path,MAX_BYTES)
        if _hash(raw_registry)!=mapping['registry_sha256']: raise ValueError('registry metadata changed')
        registry=_json(raw_registry); fields(registry,'eval_id class args metrics')
        text(registry['eval_id']); fields(registry['args'],'samples_jsonl'); text(registry['args']['samples_jsonl'])
        if registry['class']!='evals.elsuite.basic.match:Match' or registry['metrics']!=['accuracy']:
            raise ValueError('only pinned data-only Match registry metadata is supported')
    elif registry_path is not None or mapping['registry_sha256'] is not None: raise ValueError('unexpected registry metadata')
    if mapping['format']==FORMATS[0]:
        values=_json(raw_source)
        if not isinstance(values,list) or not 0<len(values)<=1000: raise ValueError('inline case array required')
    else:
        lines=raw_source.splitlines()
        if not 0<len(lines)<=1000 or any(not row.strip() for row in lines): raise ValueError('complete bounded JSONL records required')
        values=[_json(row) for row in lines]
    if {i['record'] for i in mapping['items']}!=set(range(1,len(values)+1)):
        raise ValueError('every source record requires exactly one explicit mapping')
    validator={FORMATS[0]:_case,FORMATS[1]:_result,FORMATS[2]:_sample}[mapping['format']]
    for value in values: validator(value)
    if mapping['format']==FORMATS[1] and len({(v['testIdx'],v['promptIdx']) for v in values})!=len(values):
        raise ValueError('ambiguous foreign result indices')
    identity=_hash(encoded(dict(profile=PROFILE,mapping_engine_sha256=engine_sha,mapping_sha256=_hash(raw_mapping),source_sha256=_hash(raw_source),
        registry_sha256=None if raw_registry is None else _hash(raw_registry))))
    records=[]; proposals={}
    for item in sorted(mapping['items'],key=lambda v:v['record']):
        value=values[item['record']-1]
        item_sha=_hash(encoded(dict(import_sha256=identity,mapping=item,foreign=value)))
        row=dict(item_sha256=item_sha,mapping=item,foreign_claims=value,ahl_outcome=None,
            grade_provenance='external_reported_unverified',comparison_status='not_comparable',
            comparison_reason='no_sealed_AHL_plan_runtime_and_oracle_execution_binding')
        if mapping['format']!=FORMATS[1]: proposals[item_sha]=_proposal(item,mapping,item_sha)
        records.append(row)
    missing=[key for key,value in mapping['runtime'].items() if value is None]
    missing_claims=[]
    if mapping['format']==FORMATS[1]:
        for row in records:
            grade=row['foreign_claims'].get('gradingResult')
            if grade is None: missing_claims.append(row['item_sha256']+':gradingResult')
            elif 'componentResults' not in grade: missing_claims.append(row['item_sha256']+':componentResults')
    report=dict(schema_version=1,profile=PROFILE,import_sha256=identity,format=mapping['format'],items=len(records),
        mapping_engine_sha256=engine_sha,source_sha256=_hash(raw_source),mapping_sha256=_hash(raw_mapping),registry_sha256=mapping['registry_sha256'],
        retained_fields=['source and item identity','foreign data/claims','declared license/review/runtime','explicit lineage/exposure mappings'],
        dropped_fields=[],missing_fields=['AHL reproduction','AHL oracle','authenticated lineage',*['runtime.'+key for key in missing],*missing_claims],
        unsupported_features=['foreign execution','automatic task/grader generation','foreign-to-AHL grade conversion'],
        inferred_mappings=[],source_provenance='operator_declared_unverified',review_identity='operator_attested_unverified',
        independence='unverified',bank_admission=False,automatic_promotion=False,publication_allowed=False,
        reference_semantics='prefix_match_reported_not_executed' if registry is not None else 'foreign_assertions_reported_not_executed')
    snapshots={'mapping.json':raw_mapping,'source.data':raw_source,'engine.json':encoded(evaluator)}
    if raw_registry is not None: snapshots['registry.json']=raw_registry
    return report,dict(profile=PROFILE,mapping=mapping,registry=registry,records=records),proposals,snapshots


def inspect(mapping_path, source_path, *, registry_path=None):
    return _capture(mapping_path,source_path,registry_path)[0]


def import_local(mapping_path, source_path, output, *, registry_path=None):
    report,claims,proposals,snapshots=_capture(mapping_path,source_path,registry_path)
    output=Path(output).absolute()
    if '..' in output.parts or any(p.is_symlink() for p in (output,*output.parents)): raise ValueError('canonical private import output required')
    inputs=[Path(p).absolute() for p in (mapping_path,source_path,registry_path) if p is not None]
    if any(output==p or output.is_relative_to(p.parent) or p.is_relative_to(output) for p in inputs):
        raise ValueError('import output and source directories must not overlap')
    expected={**snapshots,'report.json':encoded(report),'claims.json':encoded(claims),
        **{'proposals/'+key+'.json':encoded(value) for key,value in proposals.items()}}
    if sum(map(len,expected.values()))>8*MAX_BYTES: raise ValueError('private import output byte bound exceeded')
    def unchanged():
        if _capture(mapping_path,source_path,registry_path)[0]!=report: raise ValueError('interchange inputs changed during import')
    if output.exists():
        if set(tree(output,max_files=3000,max_bytes=8*MAX_BYTES))!=set(expected): raise ValueError('existing import output changed')
        directories={output}
        for name in expected: directories.update((output/name).parents)
        directories={p for p in directories if p==output or p.is_relative_to(output)}
        observed={output}
        for index,path in enumerate(output.rglob('*')):
            if index>=5000 or len(path.relative_to(output).parts)>64: raise ValueError('private import inventory bound exceeded')
            mode=path.lstat().st_mode
            if stat.S_ISDIR(mode): observed.add(path)
            elif not stat.S_ISREG(mode): raise ValueError('private import contains an unexpected file type')
        if observed!=directories: raise ValueError('existing import directory inventory changed')
        for path in directories:
            info=path.stat()
            if info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o700:
                raise ValueError('existing import directory ownership/mode changed')
        for name,raw in expected.items():
            path=contained(output,name); info=path.stat()
            if info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o600 or read_regular(path,8*MAX_BYTES)!=raw:
                raise ValueError('existing private import ownership/bytes/modes changed')
        unchanged(); return report
    output.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.interchange-',dir=output.parent))
    try:
        for name,raw in expected.items():
            path=stage/name; path.parent.mkdir(parents=True,exist_ok=True,mode=0o700); write_bytes(path,raw)
        if (stage/'proposals').exists(): sync_directory(stage/'proposals')
        sync_directory(stage); unchanged(); stage.rename(output); sync_directory(output.parent)
    finally:
        if stage.exists(): shutil.rmtree(stage)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(prog='ahl bank-interchange',description=__doc__)
    parser.add_argument('operation',choices=('inspect','import'))
    for name in ('mapping','source'): parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--registry',type=Path); parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    try:
        if (args.operation=='import')!=(args.output is not None): raise ValueError('only import requires a new private output')
        result=inspect(args.mapping,args.source,registry_path=args.registry) if args.operation=='inspect' else import_local(
            args.mapping,args.source,args.output,registry_path=args.registry)
        print(json.dumps(result,indent=2,allow_nan=False)); return 0
    except (ValueError,OSError,TypeError,KeyError):
        parser.exit(2,'evaluation interchange rejected: invalid, unsupported or unavailable private input\n')
