"""Live closed-input custody for the conditional bank protocol.

Serialized projections describe observations. Only the issuing process and live
workflow scope can join them to later owned construction outputs.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat
from weakref import WeakKeyDictionary

from agent_harness_lab.private_journal import encoded
from .bank_requalification_domain import parse,PROJECT_INPUT_PROFILE,PROJECT_INPUT_PATHS
from .comparison_custody import snapshot_pin
from .construction_access import store_identity,current_store_identity  # noqa: F401 - re-exported for existing importers
from .decision_receipts import engine
from .integrity import read_regular, regular_sha256
from .working_state import WorkingState
from .certification_access import check_deadline

_LIVE=WeakKeyDictionary()


def pin(value):return hashlib.sha256(encoded(value)).hexdigest()


def tree_pin(path,deadline):
    check_deadline(deadline)
    value=snapshot_pin(WorkingState(path,deadline).snapshot)
    check_deadline(deadline)
    return value


def project_selection(project):
    from .certification_access import _path
    return dict(profile=PROJECT_INPUT_PROFILE,root=str(_path(project)),paths=list(PROJECT_INPUT_PATHS))


def project_snapshot(project,selection,deadline):
    """Capture the complete declared read set, never unrelated checkout files."""
    from .certification_access import _path
    from .working_state import Snapshot
    from agent_harness_lab.workflow import harness_files
    project=_path(project);check_deadline(deadline)
    if selection!=project_selection(project):raise ValueError('unsupported or different project input selection')
    info=project.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode&0o7000:raise ValueError('regular project input root required')
    files=[];total=0
    for path in harness_files(project,allowed_paths=selection['paths']):
        check_deadline(deadline);_path(path);info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_mode&0o7000:
            raise ValueError('project inputs require bounded regular single-link files')
        _owned(info,'project inputs')
        limit=65536 if path.name=='ahl.json' else 1024*1024-total
        raw=read_regular(path,limit);total+=len(raw)
        if total>1024*1024:raise ValueError('selected project input byte bound exceeded')
        files.append((path.name,stat.S_IMODE(info.st_mode),raw))
    if 'ahl.json' not in {name for name,_,_ in files}:raise ValueError('full project configuration input required')
    check_deadline(deadline)
    return Snapshot(stat.S_IMODE(project.stat().st_mode),(),tuple(files))


def _owned(info,label):
    """Signed bytes must not be replaceable by another local user between pins."""
    if info.st_uid!=os.geteuid() or info.st_mode&0o022:
        raise ValueError(label+' must be owned by the invoking user and not group or world writable')


def check_project(project,declaration,deadline):
    snapshot=project_snapshot(project,declaration['project_inputs'],deadline)
    if snapshot_pin(snapshot)!=declaration['project_sha256']:raise ValueError('signed project inputs changed')
    return snapshot


def source_pin(source,deadline):
    from .certification_access import _path
    path=_path(source['selection']);check_deadline(deadline)
    if source['kind']=='native_source_root':return tree_pin(path,deadline)
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_mode&0o7000:
        raise ValueError('closed source requires a regular single-link input')
    _owned(info,'closed source')
    identity=regular_sha256(path,32*1024*1024);check_deadline(deadline)
    return pin(dict(mode=stat.S_IMODE(info.st_mode),sha256=identity))


class ClosedDomain:
    """Opaque same-invocation authority; copying a declaration does not issue it."""
    pass


def issue(verified):
    from .bank_requalification import _take_delegation
    declaration,project,history,deadline,authentication,initial_state=_take_delegation(verified)
    from agent_harness_lab.cli import assets
    check_deadline(deadline)
    if deadline is None:raise ValueError('finite closed-input parent deadline required')
    value=parse(declaration);project=Path(project).absolute()
    check_project(project,value,deadline)
    if (pin(engine())!=value['engine_sha256']
            or tree_pin(assets()/'skills',deadline)!=value['methodology_sha256']
            or current_store_identity(history.root)!=value['history_identity_sha256']
            or any(source_pin(source,deadline)!=source['snapshot_sha256'] for source in value['sources'])):
        raise ValueError('signed closed inputs differ from actual configured sources')
    handle=ClosedDomain()
    _LIVE[handle]=dict(pid=os.getpid(),declaration=value,project=project,history=history,deadline=deadline,
        authentication=deepcopy(authentication),initial_state=deepcopy(initial_state),scope=None,
        projection=None,views=None,access=None,closed=False,stages=[],feedback=None,candidate=None)
    return handle


def _registered(handle):
    if type(handle) is not ClosedDomain or handle not in _LIVE:raise ValueError('actual live closed-input domain required')
    value=_LIVE[handle]
    if value['pid']!=os.getpid() or value['closed']:raise ValueError('closed-input domain belongs to another invocation')
    return value


def _resources(value):
    from agent_harness_lab.cli import assets
    check_deadline(value['deadline']);declaration=value['declaration']
    if (pin(engine())!=declaration['engine_sha256']
            or tree_pin(assets()/'skills',value['deadline'])!=declaration['methodology_sha256']
            or current_store_identity(value['history'].root)!=declaration['history_identity_sha256']
            or any(source_pin(source,value['deadline'])!=source['snapshot_sha256'] for source in declaration['sources'])):
        raise ValueError('closed-input source, project, engine or history changed')
    if value['scope'] is not None and not value['scope']._valid():raise ValueError('closed-input workflow scope expired')


def owned(handle):
    value=_registered(handle);_resources(value)
    check_project(value['project'],value['declaration'],value['deadline'])
    return value


def before_collection(handle,args,*,source_context=None):
    value=owned(handle)
    if getattr(args,'focus',None) is not None:
        raise ValueError('focus is unsupported by the closed input profile')
    # These inputs would reach role views without a signed closed-domain source.
    if (source_context is not None or getattr(args,'memory_manifest',None) is not None
            or getattr(args,'incident_corpus',None) is not None or getattr(args,'incident_query',None) is not None):
        raise ValueError('source context, memory and incident inputs are outside the closed input profile')
    if value['projection'] is not None or value.get('collection_started'):
        raise ValueError('closed inputs cannot be recollected')
    from agent_harness_lab.one_call import allowed_paths
    if allowed_paths(args)!=['AGENTS.md']:
        raise ValueError('closed project input profile requires the exact configured root AGENTS.md scope')
    events=getattr(args,'events',None);root=getattr(args,'source_root',None)
    if (events is None)==(root is None):raise ValueError('closed inputs require one explicitly configured reviewed source')
    selection=dict(kind='events_file' if events is not None else 'native_source_root',
        selection=str(Path(events if events is not None else root).absolute()),format=getattr(args,'format','normalized'),
        version=getattr(args,'source_version',None),session=getattr(args,'session',None))
    matches=[source for source in value['declaration']['sources'] if all(source[key]==item for key,item in selection.items())]
    if len(matches)!=1 or Path(args.project).absolute()!=value['project']:
        raise ValueError('configured capture selection is outside the signed closed domain')
    value['selection']=matches[0];value['collection_started']=True


def collected(handle,data):
    value=owned(handle)
    if not value.get('collection_started') or value['projection'] is not None:
        raise ValueError('actual closed collection required')
    if data.get('evaluation_outcomes') or len(data.get('sources',[]))!=1:
        raise ValueError('additional or unknown collection sources are outside the signed domain')
    value['projection']=dict(profile='owned-evidence-views-v1',domain_sha256=pin(value['declaration']),
        selected_source=deepcopy(value['selection']),evidence_sha256=pin(data))


def bind_scope(handle,scope,baseline):
    value=owned(handle)
    if value['scope'] is not None or value['projection'] is None or not scope._valid():
        raise ValueError('one actual closed-input workflow scope required')
    if tree_pin(baseline,value['deadline'])!=value['declaration']['baseline_sha256']:
        raise ValueError('actual baseline differs from signed closed domain')
    value['scope']=scope


def bind_views(handle,data,views):
    value=owned(handle)
    if value['scope'] is None or value['views'] is not None or value['projection']['evidence_sha256']!=pin(data):
        raise ValueError('actual deterministic evidence projection required')
    value['views']={role:dict(path=str(Path(path).absolute()),snapshot_sha256=tree_pin(path,value['deadline']))
        for role,path in views.items()}
    value['projection']['views']=deepcopy(value['views'])


def attach(handle,access):
    value=owned(handle)
    if value['access'] is not None or value['views'] is None:raise ValueError('one owned construction workflow required')
    value['access']=access


def _refinement_snapshot(original,context,binding,feedback,deadline):
    if tree_pin(original['path'],deadline)!=original['snapshot_sha256']:
        raise ValueError('original role view differs from the stored full projection')
    if (binding is None or binding['path']!=str(Path(context).absolute())
            or binding['feedback_sha256']!=pin(feedback)):
        raise ValueError('actual controller-written feedback context required')
    expected=binding['file'];snapshot=WorkingState(context,deadline).snapshot
    selected=tuple(row for row in snapshot.files if row[0]=='feedback.json')
    if expected[0]!='feedback.json' or selected!=(expected,):
        raise ValueError('feedback bytes or mode differ from the actual controller writer')
    normalized=replace(snapshot,files=tuple(row for row in snapshot.files if row!=expected))
    if snapshot_pin(normalized)!=original['snapshot_sha256']:
        raise ValueError('refinement context has unbound full snapshot inputs')
    return snapshot


def write_feedback(handle,path,rows):
    """Bind the existing controller writer's exact file to owned native feedback."""
    value=owned(handle);original=value['views']['proposer'];path=Path(path).absolute()
    if value['feedback'] is None or value['feedback']['rows']!=rows or path.name!='feedback.json' or path.exists() or path.is_symlink():
        raise ValueError('fresh controller feedback from actual native trials required')
    if (tree_pin(original['path'],value['deadline'])!=original['snapshot_sha256']
            or tree_pin(path.parent,value['deadline'])!=original['snapshot_sha256']):
        raise ValueError('feedback insertion requires the unchanged full owned role view')
    raw=(json.dumps(dict(trials=rows),indent=2,allow_nan=False)+'\n').encode()
    binding=dict(path=str(path.parent),file=('feedback.json',0o600,raw),feedback_sha256=pin(value['feedback']))
    from agent_harness_lab.workflow import write_json
    write_json(path,dict(trials=rows))
    _refinement_snapshot(original,path.parent,binding,value['feedback'],value['deadline'])
    value['feedback_context']=binding


def stage_input(handle,access,role,context,*,parent=None,feedback=None):
    value=owned(handle)
    if value['access'] is not access or value['views'] is None:raise ValueError('actual closed workflow required')
    original=value['views'][role]
    if parent is None:
        if str(Path(context).absolute())!=original['path'] or tree_pin(context,value['deadline'])!=original['snapshot_sha256']:
            raise ValueError('construction context is outside the actual owned projection')
    else:
        # Only the existing WorkflowAccess.feedback path supplies this actual edge.
        if role!='proposer' or feedback is None or not any(row['construction_id']==parent for row in value['stages']):
            raise ValueError('complete same-workflow feedback closure required')
        if value['feedback'] is None or value['feedback']['rows']!=feedback:
            raise ValueError('actual completed native trial feedback required')
        _refinement_snapshot(original,context,value.get('feedback_context'),value['feedback'],value['deadline'])
    return dict(projection=deepcopy(value['projection']),parent_construction_id=parent,
        feedback=None if feedback is None else deepcopy(value['feedback']))


def record_feedback(handle,rows,evaluated):
    """Join feedback to actual native outcomes before a later proposer sees it."""
    value=owned(handle);scope=value['scope']
    if scope is None:raise ValueError('actual workflow scope required')
    observed=scope.read();events={event['result_sha256']:event for event in observed['events']
        if event.get('complete') and event.get('stage')=='role'}
    actual=[];joins=[]
    if evaluated.get('inputs_unchanged') is not True:raise ValueError('unchanged native trial inputs required')
    for trial in evaluated['trials']:
        if trial.get('split') not in ('development','regression'):continue
        execution=trial.get('execution');grade=trial.get('grader',{}).get('execution')
        worker=events.get(pin(execution));grader=events.get(pin(grade))
        if (worker is None or grader is None or worker['role']!='executor' or grader['role']!='grader'
                or not any(pair['executor']==worker['dispatch_sha256'] and pair['grader']==grader['dispatch_sha256']
                    for pair in observed['pairs'])):
            raise ValueError('feedback lacks actual owned executor/grader completion')
        actual.append({key:trial.get(key) for key in ('task','version','split','status')})
        joins.append(dict(executor=worker['dispatch_sha256'],grader=grader['dispatch_sha256']))
    if not actual or actual!=rows:raise ValueError('feedback differs from actual native trials')
    value['feedback']=dict(rows=deepcopy(rows),native_pairs=joins,evidence_sha256=pin(evaluated))


def add_stage(handle,access,ident,seal,output):
    value=owned(handle)
    if value['access'] is not access:raise ValueError('actual closed workflow required')
    from .construction_seals import read
    proof=read(seal,value['scope'])
    value['stages'].append(dict(construction_id=ident,seal=seal,proof=proof,output=deepcopy(output)))


def frozen_candidate(handle,access,path,snapshot,construction_id):
    value=owned(handle)
    if value['access'] is not access or not any(row['construction_id']==construction_id for row in value['stages']):
        raise ValueError('actual same-workflow proposer candidate required')
    pin_value=snapshot_pin(snapshot)
    if tree_pin(path,value['deadline'])!=pin_value:raise ValueError('frozen candidate changed')
    value['candidate']=dict(path=str(Path(path).absolute()),snapshot_sha256=pin_value,construction_id=construction_id)


def closure(handle):
    value=owned(handle)
    if value['access'] is None or not value['stages']:raise ValueError('completed owned development closure required')
    from .construction_seals import read
    stages=[]
    for row in value['stages']:
        if read(row['seal'],value['scope'])!=row['proof']:raise ValueError('owned construction seal changed')
        stages.append(dict(construction_id=row['construction_id'],proof=row['proof'],output=row['output']))
    return value,dict(profile='owned-closed-input-closure-v1',domain_sha256=pin(value['declaration']),
        projection=deepcopy(value['projection']),stages=stages)


def close(handle):
    value=_LIVE.get(handle)
    if value is not None and value['pid']==os.getpid():
        value['closed']=True;_LIVE.pop(handle,None)


def check(handle):
    """Return current descriptive bindings without exporting live authority."""
    value=owned(handle)
    return dict(profile=value['declaration']['profile'],domain_sha256=pin(value['declaration']),
        sources=deepcopy(value['declaration']['sources']),history_store=str(value['history'].root),
        scope_bound=value['scope'] is not None,deadline=value['deadline'],
        external_exposure='unknown',pretraining_exposure='unknown')

