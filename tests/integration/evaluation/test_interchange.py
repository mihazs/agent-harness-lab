"""Independently synthetic local data; no foreign evaluators/providers are executed."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest

import agent_harness_lab
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import interchange as module
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.evaluation.regressions import load
from tests.integration.evaluation import test_regression_review as review_fixtures


def digest(raw): return hashlib.sha256(raw).hexdigest()


class Interchange:
    """A synthetic foreign source, its declared mapping and the import output folder."""

    def __init__(self, root):
        self.root=root; self.inputs=self.root/'inputs'; self.inputs.mkdir()
        self.source=self.inputs/'source.json'; self.mapping=self.inputs/'mapping.json'
        self.output=self.root/'imported'; self.registry=None
        self.values=[dict(vars={'question':'Return the synthetic numeral four'},
            **{'assert':[dict(type='equals',value='4'),dict(type='contains',value='4',weight=0)],'threshold':.5})]
        self.value=dict(schema_version=1,format=module.FORMATS[0],source=dict(repository='synthetic:independent-public-fixture',
            revision='a'*40,format_version='inline-json-subset-v1',dataset_id='synthetic-four',source_kind='synthetic',
            permission='approved',dataset_license=dict(id='CC0-1.0',ref='synthetic:fixture-author-dedication'),sha256='a'*64),
            review=dict(identity='synthetic-operator',source='operator_attestation',evidence_refs=[dict(ref='synthetic:review',sha256='b'*64)]),
            runtime=dict(provider=None,model_revision=None,configuration_sha256=None),score_descriptor=None,registry_sha256=None,
            items=[dict(record=1,source_item_id='four',task_id='four',root_id='related-four',family='synthetic-text',
                source_split='foreign-dev',partition='construction',exposure='development',expected_boundary='Return the numeral four.')])
        self.write()

    def write(self):
        raw=(json.dumps(self.values)+'\n').encode() if self.value['format']==module.FORMATS[0] else b''.join(
            (json.dumps(v)+'\n').encode() for v in self.values)
        self.source.write_bytes(raw); self.value['source']['sha256']=digest(raw)
        self.mapping.write_text(json.dumps(self.value))

    def import_data(self, output=None):
        return module.import_local(self.mapping,self.source,output or self.output,registry_path=self.registry)

    def saved(self): return json.loads((self.output/'claims.json').read_bytes())

    def openai(self):
        self.value['format']=module.FORMATS[2]; self.values=[dict(input=[dict(role='user',content='Return four.')],ideal=['4'])]
        self.registry=self.inputs/'registry.json'
        self.registry.write_text(json.dumps(dict(eval_id='synthetic.dev.v1',**{'class':'evals.elsuite.basic.match:Match'},
            args=dict(samples_jsonl='never-follow/samples.jsonl'),metrics=['accuracy'])))
        self.value['registry_sha256']=digest(self.registry.read_bytes()); self.write()


@pytest.fixture
def ix(tmp_path):
    return Interchange(tmp_path)


def test_actual_cli_promptfoo_import_produces_existing_unknown_proposal_without_execution(ix):
    stdout=io.StringIO()
    with patch('socket.socket',side_effect=AssertionError('no network')), \
            patch('subprocess.Popen',side_effect=AssertionError('no execution')),redirect_stdout(stdout):
        code=main(['bank-interchange','import','--mapping',str(ix.mapping),'--source',str(ix.source),'--output',str(ix.output)])
    assert code == 0; report=json.loads(stdout.getvalue())
    assert ix.saved()['records'][0]['foreign_claims'] == ix.values[0]
    source=next((ix.output/'proposals').glob('*.json')); proposal,pin=load(source)
    receipt=proposal.receipt(pin)
    assert receipt['declared_disposition'] == 'insufficient_evidence'; assert not receipt['bank_admission']
    assert not report['publication_allowed']; assert report['independence'] == 'unverified'
    assert 'before.harness_sha256' in receipt['missing_evidence']
    assert ix.output.stat().st_mode&0o777 == 0o700
    for path in ix.output.rglob('*'):
        assert path.stat().st_mode&0o777 == (0o700 if path.is_dir() else 0o600)
    with redirect_stdout(io.StringIO()): assert main(['regression','--proposal',str(source)]) == 0


def test_openai_registry_samples_preserve_prefix_reference_semantics_without_class_loading(ix):
    ix.openai()
    with patch('socket.socket',side_effect=AssertionError('no network')),patch('subprocess.Popen',side_effect=AssertionError('no execution')):
        report=ix.import_data()
    assert report['reference_semantics'] == 'prefix_match_reported_not_executed'
    assert ix.saved()['records'][0]['foreign_claims']['ideal'] == ['4']
    assert '4 extra'.startswith('4'); assert '4 extra' != '4'
    assert not (ix.inputs/'never-follow').exists()
    assert len(list((ix.output/'proposals').glob('*.json'))) == 1


def test_foreign_weighted_components_and_contradictions_are_claims_not_boolean_grades(ix):
    ix.value['format']=module.FORMATS[1]
    ix.value['score_descriptor']=dict(metric='foreign-weighted',kind='aggregate',scale=dict(minimum=0,maximum=1,unit='ratio'),
        aggregation='case',judge_ref=None)
    ix.values=[dict(testIdx=0,promptIdx=0,success=False,score=1/3,response=dict(output='Goodbye world'),
        gradingResult={'pass':False,'score':1/3,'componentResults':[
            {'pass':False,'score':0,'assertion':dict(type='equals',value='Hello world',weight=2)},
            {'pass':True,'score':1,'assertion':dict(type='contains',value='world',weight=1)}]})]
    ix.write(); ix.import_data(); row=ix.saved()['records'][0]
    assert row['foreign_claims'] == ix.values[0]; assert bool(row['foreign_claims']['score'])
    assert not row['foreign_claims']['success']; assert row['ahl_outcome'] is None
    assert row['comparison_status'] == 'not_comparable'; assert not (ix.output/'proposals').exists()


def test_identical_reimport_is_idempotent_changed_bytes_mapping_registry_have_new_identity(ix):
    first=ix.import_data(); frozen={str(p.relative_to(ix.output)):p.read_bytes() for p in ix.output.rglob('*') if p.is_file()}
    assert first == ix.import_data(); assert frozen == {str(p.relative_to(ix.output)):p.read_bytes() for p in ix.output.rglob('*') if p.is_file()}
    ix.value['items'][0]['expected_boundary']='A different declared boundary.'; ix.write()
    assert first['import_sha256'] != ix.import_data(ix.root/'changed')['import_sha256']
    with pytest.raises(ValueError): ix.import_data()
    ix.openai(); before=ix.import_data(ix.root/'registry-one')
    value=json.loads(ix.registry.read_bytes()); value['eval_id']='synthetic.dev.v2'; ix.registry.write_text(json.dumps(value))
    ix.value['registry_sha256']=digest(ix.registry.read_bytes()); ix.write()
    assert before['import_sha256'] != ix.import_data(ix.root/'registry-two')['import_sha256']


def test_selected_layout_import_is_actual(ix):
    expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
    if expected: assert Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve())


def test_known_private_markers_reject_forged_public_provenance(ix):
    report=ix.import_data(); claims=ix.saved()
    public=dict(source_kind='synthetic',approved_publication=True,source_ref='synthetic:public',derived_from_private=False)
    proposal=json.loads(next((ix.output/'proposals').glob('*.json')).read_bytes())
    for value in (report,claims,*claims['records'],ix.value,proposal):
        for wrapper in (None,'receipt','analysis','context'):
            forged=dict(value,publication_allowed=True,provenance=public)
            row=forged if wrapper is None else {wrapper:forged,'provenance':public}
            assert not public_export_allowed([row])


@pytest.mark.parametrize('format_name',[pytest.param(name,id=name) for name in (module.FORMATS[0],module.FORMATS[2])])
def test_both_case_profiles_require_supplied_reproduction_then_existing_review_admission_and_export(ix,format_name):
    from agent_harness_lab.evaluation.regression_review import ReviewStore
    from agent_harness_lab.evaluation.bank_tools import export_manifest
    from agent_harness_lab.evaluation.task_bank import validate
    with ExitStack() as cleanups:
        fixture=review_fixtures.ReviewFixture(cleanups.callback)
        replay=fixture.fixture; provided=json.loads(replay.source.read_bytes())
        descriptor=json.loads(replay.draft.read_bytes()); task=descriptor['task']
        ix.value['items'][0].update(task_id=task['task_id'],root_id=task['root_id'],family=task['family'],
            expected_boundary=provided['expected_boundary'])
        prompt=json.loads((replay.root/'fixture/task.json').read_bytes())['prompt']
        if format_name==module.FORMATS[2]:
            ix.openai(); ix.values[0]['input']=prompt
        else:
            ix.value.update(format=format_name,registry_sha256=None); ix.registry=None
            ix.values=[dict(vars={'question':prompt},**{'assert':[dict(type='llm-rubric',value=provided['expected_boundary'])]})]
        ix.write(); destination=ix.root/format_name; ix.import_data(destination)
        imported=next((destination/'proposals').glob('*.json')); raw_imported=imported.read_bytes()
        declared=json.loads(raw_imported); replay.source.write_bytes(raw_imported)
        descriptor['task']['source_ref']='external-evaluation:'+declared['proposal_id']
        replay.draft.write_text(json.dumps(descriptor)); replay.bind()
        incomplete=ReviewStore(replay.root/'incomplete-import')
        reported=incomplete.propose(replay.source,replay.draft,replay.spec,'synthetic-author')
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=fixture.backend):
            failed=incomplete.reproduce(reported['case_id'],fixture.backend)
        assert failed['state'] == 'reported'; fixture.backend.run.assert_not_called()
        # These bytes are provided by the independent synthetic fixture factory,
        # never inferred from a foreign ideal, pass label or score.
        for key in ('before','after','task_start','environment','reproduction'): declared[key]=provided[key]
        declared['source']['trace_refs']=provided['source']['trace_refs']
        replay.source.write_text(json.dumps(declared)); replay.bind()
        store=ReviewStore(replay.root/'complete-import')
        case=store.propose(replay.source,replay.draft,replay.spec,'synthetic-author')['case_id']
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=fixture.backend):
            observed=store.reproduce(case,fixture.backend)
        assert observed['state'] == 'reproduced'; assert fixture.backend.run.call_count == 21
        event=next(e for e,_ in store.history(store.case(case)) if e['kind']=='reproduced')
        actual=json.loads((store.case(case)/event['payload']['attempt']/'replay/replay.json').read_bytes())
        assert not actual['phases']['ab']['outcomes']['recorded/baseline']['outcome']
        assert actual['phases']['ab']['outcomes']['recorded/candidate']['outcome']
        current=store.draft(case)
        review=json.loads(fixture.review_file().read_bytes())
        review.update(case_id=case,expected_event_sha256=current['event_sha256'],evidence_sha256=current['evidence_sha256'])
        review_path=replay.root/'new-review.json'; review_path.write_text(json.dumps(review)); store.review(case,review_path)
        parent=replay.root/'parent'; source=Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
        shutil.copytree(source,parent); bank=json.loads((parent/'bank.json').read_bytes())
        bank['tasks']=[t for t in bank['tasks'] if t['task_id']=='resumption']; (parent/'bank.json').write_text(json.dumps(bank))
        current=store.show(case)
        request=dict(schema_version=1,case_id=case,expected_event_sha256=current['event_sha256'],
            evidence_sha256=current['evidence_sha256'],parent_bank_sha256=digest((parent/'bank.json').read_bytes()),
            parent_revision_sha256=None,operator=dict(identity='synthetic-curator',source='operator_attestation',
                provenance='Provided synthetic artifact review',basis='Review actual frozen controls and replay.'),
            purpose='development_regression',provenance_binding=None,reason='Synthetic data-only workflow control.')
        request_path=replay.root/'accept.json'; request_path.write_text(json.dumps(request))
        accepted=store.accept(case,parent/'bank.json',request_path)
        admitted=store.root/'revisions'/accepted['revision_sha256']/'bank.json'
        exported=export_manifest(admitted,replay.root/'export')
        assert not validate(admitted)['certification_eligible']; assert not exported['independence_verified']
        assert imported.read_bytes() == raw_imported
        assert observed['historical_verification'] == 'unverified'


@pytest.mark.parametrize('edit',[
    pytest.param(dict(providers=['synthetic:must-not-run']),id='providers'),
    pytest.param(dict(options={'provider':'synthetic:must-not-run'}),id='options-provider'),
    pytest.param(dict(assertScoringFunction='file://never.py'),id='scoring-function'),
    pytest.param(dict(vars={'api_key':'SYNTHETIC_SECRET'}),id='credential-var'),
    pytest.param(dict(vars={'question':'file://never-read.txt'}),id='file-var'),
    pytest.param(dict(vars={'question':'{{ unsafe() }}'}),id='template-var'),
    pytest.param({'assert':[dict(type='javascript',value='throw new Error("never")')]},id='javascript-assert'),
    pytest.param({'assert':[dict(type='python',value='file://never.py')]},id='python-assert'),
    pytest.param({'assert':[dict(type='equals',value='https://synthetic.invalid/not-fetched')]},id='remote-value')])
def test_executable_sensitive_templates_and_remote_config_reject_before_output(ix,edit):
    ix.values=[dict(ix.values[0],**edit)]; ix.write()
    with patch('socket.socket',side_effect=AssertionError('no network')), \
            patch('subprocess.Popen',side_effect=AssertionError('no execution')),pytest.raises(ValueError):
        ix.import_data()
    assert not ix.output.exists()


@pytest.mark.parametrize('edit',[
    pytest.param(lambda v:v.update(schema_version=True),id='boolean-schema-version'),
    pytest.param(lambda v:v.update(format='future-profile'),id='future-format'),
    pytest.param(lambda v:v['source'].update(permission='unverified'),id='unverified-permission'),
    pytest.param(lambda v:v['source'].update(sha256='f'*64),id='stale-source-digest'),
    pytest.param(lambda v:v['source']['dataset_license'].update(id='unknown'),id='unknown-license'),
    pytest.param(lambda v:v['source'].pop('dataset_license'),id='missing-license'),
    pytest.param(lambda v:v['review'].update(source='verified'),id='self-verified-review'),
    pytest.param(lambda v:v['items'][0].update(partition='certification'),id='certification-partition'),
    pytest.param(lambda v:v['items'][0].update(exposure='untouched'),id='untouched-exposure'),
    pytest.param(lambda v:v['items'][0].update(record=True),id='boolean-record'),
    pytest.param(lambda v:v['items'].append(deepcopy(v['items'][0])),id='duplicate-item')])
def test_mapping_permission_license_lineage_ids_and_versions_fail_closed(ix,edit):
    edit(ix.value); ix.mapping.write_text(json.dumps(ix.value))
    with pytest.raises((ValueError,KeyError)): ix.import_data()
    assert not ix.output.exists()


@pytest.mark.parametrize('payload',[
    pytest.param(lambda original:b'[{"vars":{},"vars":{},"assert":[]}]',id='duplicate-key'),
    pytest.param(lambda original:b'[{"vars":{"x":NaN},"assert":[]}]',id='non-finite'),
    pytest.param(lambda original:b'[{"vars":{"x":"\\ud800"},"assert":[]}]',id='lone-surrogate'),
    pytest.param(lambda original:b'['*64+b'{}'+b']'*64,id='too-deep'),
    pytest.param(lambda original:original[:-2],id='truncated'),
    pytest.param(lambda original:b' '*(module.MAX_BYTES+1),id='oversize')])
def test_malformed_duplicate_nonfinite_surrogate_deep_truncated_oversize_reject(ix,payload):
    raw=payload(ix.source.read_bytes())
    ix.source.write_bytes(raw); ix.value['source']['sha256']=digest(raw); ix.mapping.write_text(json.dumps(ix.value))
    with pytest.raises(ValueError): ix.import_data()
    assert not ix.output.exists()


def test_linked_sources_reject(ix):
    target=ix.inputs/'target.json'; ix.source.rename(target); ix.source.symlink_to(target)
    with pytest.raises(ValueError): ix.import_data()
    ix.source.unlink(); os.link(target,ix.source)
    with pytest.raises(ValueError): ix.import_data()


def test_changed_source_during_capture_cannot_publish_and_changed_snapshot_cannot_reimport(ix):
    original=module._capture
    def changed(*args):
        result=original(*args); ix.source.write_bytes(ix.source.read_bytes()+b' '); return result
    with patch.object(module,'_capture',side_effect=changed),pytest.raises(ValueError): ix.import_data()
    assert not ix.output.exists(); assert not list(ix.root.glob('.interchange-*'))
    ix.write(); ix.import_data(); (ix.output/'source.data').write_bytes(b'changed')
    with pytest.raises(ValueError): ix.import_data()


def test_openai_unknown_class_missing_metadata_and_stale_registry_reject_without_loading(ix):
    ix.openai(); original=ix.registry.read_bytes()
    for change in (dict(**{'class':'synthetic.module:RunNever'}),dict(args={'samples_jsonl':'x','setup':'never'})):
        value=json.loads(original); value.update(change); ix.registry.write_text(json.dumps(value))
        ix.value['registry_sha256']=digest(ix.registry.read_bytes()); ix.write()
        with pytest.raises(ValueError): ix.import_data()
        assert not ix.output.exists()
    ix.registry.write_bytes(original); ix.value['registry_sha256']='f'*64; ix.write()
    with pytest.raises(ValueError): ix.import_data()


def reported_result(ix):
    ix.value['format']=module.FORMATS[1]
    ix.value['score_descriptor']=dict(metric='reported',kind='unknown',scale=dict(minimum=None,maximum=None,unit='source_units'),
        aggregation='case',judge_ref=None)
    ix.values=[dict(testIdx=0,promptIdx=0,success=True,score=-.8,gradingResult=None)]
    ix.write(); return ix.import_data()


def test_result_missing_components_remain_visible(ix):
    report=reported_result(ix)
    assert any(v.endswith(':gradingResult') for v in report['missing_fields'])
    assert ix.saved()['records'][0]['foreign_claims']['score'] == -.8


@pytest.mark.parametrize(('key','value'),[pytest.param('score',True,id='score'),pytest.param('success',1,id='success'),
                                         pytest.param('testIdx',False,id='testIdx')])
def test_result_boolean_integer_aliases_reject(ix,key,value):
    reported_result(ix)
    ix.values[0][key]=value; ix.write()
    with pytest.raises(ValueError): ix.import_data(ix.root/'invalid')


def test_related_result_rows_retain_one_declared_task_without_independence_inflation(ix):
    ix.value['format']=module.FORMATS[1]
    ix.value['score_descriptor']=dict(metric='reported',kind='aggregate',scale=dict(minimum=None,maximum=None,unit='source_units'),
        aggregation='case',judge_ref='synthetic:unverified-judge')
    ix.values=[dict(testIdx=0,promptIdx=i,success=True,score=.2+i/2) for i in range(2)]
    second=deepcopy(ix.value['items'][0]); second.update(record=2,source_item_id='four-other-prompt')
    ix.value['items'].append(second); ix.write()
    report=ix.import_data(); rows=ix.saved()['records']
    assert report['items'] == 2; assert len({r['mapping']['task_id'] for r in rows}) == 1
    assert len({r['mapping']['root_id'] for r in rows}) == 1
    assert report['independence'] == 'unverified'; assert 'independent_roots' not in report
    ix.values[1]['promptIdx']=0; ix.write()
    with pytest.raises(ValueError): ix.import_data(ix.root/'ambiguous')


def test_engine_changes_invalidate_reimport_and_no_inspection_or_failed_cli_output_leaks_content(ix):
    first=ix.import_data()
    from agent_harness_lab.evaluation.decision_receipts import engine
    changed=dict(engine(),synthetic_upgrade=True)
    with patch('agent_harness_lab.evaluation.decision_receipts.engine',return_value=changed):
        assert first['import_sha256'] != module.inspect(ix.mapping,ix.source)['import_sha256']
        with pytest.raises(ValueError): ix.import_data()
    sentinel='SYNTHETIC_PRIVATE_INPUT_SENTINEL'; ix.values[0]['vars']['question']=sentinel; ix.write()
    stdout=io.StringIO()
    with redirect_stdout(stdout): assert main(['bank-interchange','inspect','--mapping',str(ix.mapping),'--source',str(ix.source)]) == 0
    assert sentinel not in stdout.getvalue(); assert str(ix.root) not in stdout.getvalue()


@pytest.mark.parametrize('kind',('file-executable','directory-world-access','unexpected-empty-directory'))
def test_frozen_output_modes_and_empty_directory_inventory_cannot_change(ix,kind):
    first=ix.import_data()
    try:
        if kind=='file-executable': (ix.output/'source.data').chmod(0o700)
        elif kind=='directory-world-access': (ix.output/'proposals').chmod(0o777)
        else: (ix.output/'extra').mkdir(mode=0o700)
        with pytest.raises(ValueError): ix.import_data()
    finally:
        (ix.output/'source.data').chmod(0o600); (ix.output/'proposals').chmod(0o700)
        if (ix.output/'extra').exists(): (ix.output/'extra').rmdir()
    assert first == ix.import_data()
