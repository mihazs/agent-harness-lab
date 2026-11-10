"""Convert one live isolated author output into a descriptive frozen task bank."""
from pathlib import Path
import tempfile
from weakref import WeakKeyDictionary

from agent_harness_lab.private_journal import encoded, record, write_bytes
from .integrity import artifacts, contained, copy_frozen, digest
from .bank_revisions import _copy_assets
from .task_bank import _validate


# These fields are declarations for later signed review, not controller findings.
METADATA = frozenset(('root_id', 'family', 'project_group', 'time_group', 'grader_version',
                      'source_kind', 'source_ref', 'permission', 'reviewer', 'review_status',
                      'independence', 'safety'))
_ISSUED=WeakKeyDictionary()


class _Handoff:
    pass


def consume(handle,bank_path,controls_sha256):
    from . import construction_access as access
    from .construction_seals import read
    if type(handle) is not _Handoff or handle not in _ISSUED:
        raise ValueError('actual live author conversion handoff required')
    value=_ISSUED.pop(handle)
    owned=access._STAGES.get(value['stage'])
    if owned is None:raise ValueError('author stage expired before intake')
    access._check(owned,pending=False)
    proof=read(value['seal'],value['scope'])
    if (Path(bank_path).absolute()!=value['bank_path'] or _validate(bank_path)['bank_sha256']!=value['bank_sha256']
            or controls_sha256!=value['controls_sha256'] or proof!=value['proof']):
        raise ValueError('author conversion or controls changed before intake')
    return value['custody']


def build(source_root, metadata, target):
    source, _ = record(Path(source_root)/'manifest.json', 1024*1024)
    selected = [task for task in source['tasks'] if task.get('split') == 'heldout']
    if not selected or type(metadata) is not dict or set(metadata) != {task['id'] for task in selected}:
        raise ValueError('metadata must cover exactly the authored held-out tasks')
    tasks=[];accounting=[0,0]
    for task in selected:
        detail=metadata[task['id']]
        if type(detail) is not dict or set(detail)!=METADATA:
            raise ValueError('exact task-bank declarations required')
        bundle=source['grader_bundles'][task['id']]['path']
        fixture=contained(source_root,task['fixture'])
        fixture_task,_=record(fixture/'task.json',1024*1024)
        if not isinstance(fixture_task.get('prompt'),str) or not fixture_task['prompt']:
            raise ValueError('authored bank fixture requires a prompt')
        value=dict(detail,task_id=task['id'],partition='certification',fixture=task['fixture'],
            fixture_digest=digest(fixture),grader_bundle=bundle,
            grader_digest=digest(contained(source_root,bundle)),artifacts=task['artifacts'])
        from .bank_qualification_controls import record_controls
        record_controls(contained(source_root,bundle))
        tasks.append(_copy_assets(Path(source_root),value,target,accounting))
    path=Path(target)/'bank.json'
    write_bytes(path,encoded(dict(schema_version=1,separate_by=['root_id'],tasks=tasks)))
    checked=_validate(path)
    if checked['issues']:
        raise ValueError('converted task-bank declarations or content grouping incomplete')
    return path,checked['bank_sha256']


def check_unexposed(bank,controls,heldout,seen):
    from .bank_tools import materialize_sources
    bank_value,_=record(bank/'bank.json',1024*1024)
    for task in bank_value['tasks']:
        with tempfile.TemporaryDirectory() as work:
            original=Path(work)/'original'
            copy_frozen(contained(bank,task['fixture']),original,task['fixture_digest'])
            materialize_sources(original)
            existing=[name for name in task['artifacts'] if contained(original,name).is_file()]
            before=artifacts(original,existing)
            for index,item in enumerate(controls['tasks'][task['task_id']]):
                probe=Path(work)/str(index)
                copy_frozen(contained(heldout,item['path']),probe,item['sha256'])
                materialize_sources(probe)
                changed=artifacts(probe,item['artifacts'])
                if any(not raw or any(raw in content for content in seen)
                       for name,raw in changed.items() if before.get(name)!=raw):
                    raise ValueError('held-out artifact was present in an author input')


def convert(stage,seal,scope,metadata,controls_manifest,output,review_store,declaration):
    """Use the live author stage and actual native seal; never accept serialized proof."""
    from . import construction_access as access
    from .construction_seals import read
    from .bank_heldout import freeze, read as read_controls
    from .working_state import WorkingState
    from .comparison_custody import snapshot_pin
    if type(stage) is not access._Stage or stage not in access._STAGES:
        raise ValueError('actual live author stage required')
    owned=access._STAGES[stage]
    access._check(owned,pending=False)
    if owned['role']!='evaluator_author' or not owned.get('published'):
        raise ValueError('completed author stage required')
    proof=read(seal,scope)
    if (proof['role']!='evaluator_author' or not proof['boundary_required']
            or not proof['native'] or any(row.get('boundary',{}).get('status')!='qualified' for row in proof['native'])
            or proof['destination']!=str(owned['active']/'published')
            or proof['input_snapshots']!={name:snapshot_pin(WorkingState(owned[source],owned['deadline']).snapshot)
                                         for name,source in (('harness','baseline'),('fixture','context'))}
            or access._tree_pin(owned['active']/'published',owned['deadline'])!=owned['output'].get('closed_input',{}).get('producer_tree_sha256')):
        raise ValueError('qualified native author boundary required')
    controls_manifest=Path(controls_manifest).absolute();output=Path(output).absolute()
    from .review_authority import _path, _outside
    from .decision_receipts import private
    controls_manifest=_path(controls_manifest);output=_path(output)
    private(controls_manifest.parent,directory=True)
    _outside(controls_manifest.parent,[owned['baseline'],owned['context'],owned['folder'],owned['active'],output])
    _outside(output,[owned['baseline'],owned['context'],owned['folder'],owned['active'],owned['destination']])
    if output.exists():raise ValueError('new bank conversion destination required')
    declaration_value,_=record(declaration,1024*1024)
    with tempfile.TemporaryDirectory(dir=output.parent,prefix='.bank-conversion-') as folder:
        root=Path(folder);bank=root/'bank';bank.mkdir(mode=0o700)
        bank_path,bank_sha256=build(owned['folder']/'published',metadata,bank)
        controls,controls_sha256=freeze(controls_manifest,bank_path,declaration_value['author'],root/'heldout')
        # Reject exact changed artifact bytes already present in either actual author input.
        seen={raw for path in (owned['baseline'],owned['context'])
              for _,_,raw in WorkingState(path,owned['deadline']).snapshot.files}
        seen.update(raw for _,_,raw in owned['produced_state'].snapshot.files)
        check_unexposed(bank,controls,root/'heldout',seen)
        access._check(owned,pending=False);read(seal,scope);read_controls(root/'heldout',controls_sha256)
        evidence=dict(schema_version=1,bank_sha256=bank_sha256,heldout_sha256=controls_sha256,
            author_output_sha256=proof['published_sha256'],author_inputs=proof['input_snapshots'],
            native_boundary='observed',independence='review_judgment_required',publication_allowed=False)
        write_bytes(root/'conversion.json',encoded(evidence))
        root.rename(output)
    from .construction_access import _hash
    custody=dict(history_store=str(owned['store'].root),construction_id=owned['ident'],
        output_sha256=_hash(owned['output']),proof_sha256=_hash(proof),
        bank_sha256=bank_sha256,heldout_sha256=controls_sha256)
    handle=_Handoff()
    _ISSUED[handle]=dict(stage=stage,seal=seal,scope=scope,proof=proof,custody=custody,
        bank_path=output/'bank/bank.json',bank_sha256=bank_sha256,controls_sha256=controls_sha256)
    from .bank_qualification import intake
    from .regression_review import ReviewStore
    result=intake(ReviewStore(review_store),output/'bank/bank.json',declaration,controls_manifest,_conversion=handle)
    return dict(evidence=evidence,case=result)
