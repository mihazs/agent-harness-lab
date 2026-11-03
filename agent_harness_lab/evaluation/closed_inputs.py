"""Live closed-input custody for the conditional bank protocol.

Serialized projections describe observations. Only the issuing process and live
workflow scope can join them to later owned construction outputs.
"""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import stat
from weakref import WeakKeyDictionary

from agent_harness_lab.private_journal import encoded
from .bank_requalification_domain import parse,PROJECT_INPUT_PROFILE,PROJECT_INPUT_PATHS
from .comparison_custody import snapshot_pin
from .construction_access import store_identity
from .decision_receipts import engine
from .integrity import read_regular
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
        limit=65536 if path.name=='ahl.json' else 1024*1024-total
        raw=read_regular(path,limit);total+=len(raw)
        if total>1024*1024:raise ValueError('selected project input byte bound exceeded')
        files.append((path.name,stat.S_IMODE(info.st_mode),raw))
    if 'ahl.json' not in {name for name,_,_ in files}:raise ValueError('full project configuration input required')
    check_deadline(deadline)
    return Snapshot(stat.S_IMODE(project.stat().st_mode),(),tuple(files))


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
    raw=read_regular(path,32*1024*1024);check_deadline(deadline)
    return pin(dict(mode=stat.S_IMODE(info.st_mode),sha256=hashlib.sha256(raw).hexdigest()))


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
            or store_identity(history.root)!=value['history_identity_sha256']
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
            or store_identity(value['history'].root)!=declaration['history_identity_sha256']
            or any(source_pin(source,value['deadline'])!=source['snapshot_sha256'] for source in declaration['sources'])):
        raise ValueError('closed-input source, project, engine or history changed')
    if value['scope'] is not None and not value['scope']._valid():raise ValueError('closed-input workflow scope expired')


def owned(handle):
    value=_registered(handle);_resources(value)
    check_project(value['project'],value['declaration'],value['deadline'])
    return value


def before_collection(handle,args):
    value=owned(handle)
    if getattr(args,'focus',None) is not None:
        raise ValueError('focus is unsupported by the closed input profile')
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
        from .integrity import tree
        from agent_harness_lab.private_journal import record
        original_files=tree(original['path']);actual=tree(context)
        if {k:v for k,v in actual.items() if k!='feedback.json'}!=original_files or record(Path(context)/'feedback.json')[0]!={'trials':feedback}:
            raise ValueError('refinement context has unbound external inputs')
    return dict(projection=deepcopy(value['projection']),parent_construction_id=parent,
        feedback=None if feedback is None else deepcopy(value['feedback']))


def record_feedback(handle,rows,evaluated):
    """Join feedback to actual native outcomes before a later proposer sees it."""
    value=owned(handle);scope=value['scope']
    if scope is None:raise ValueError('actual workflow scope required')
    observed=scope.read();events=[event for event in observed['events']
        if event.get('complete') and event.get('stage')=='role']
    bindings=[binding for binding in observed['bindings'] if binding['manifest_sha256']==evaluated.get('manifest_sha256')]
    if len(bindings)!=1:raise ValueError('feedback requires its actual bound author manifest')
    binding=bindings[0]
    from agent_harness_lab.private_journal import record
    from agent_harness_lab.cli import evaluator
    from .integrity import contained
    manifest,manifest_pin=record(binding['manifest'],1024*1024)
    if manifest_pin!=binding['manifest_sha256'] or 'experiment' in manifest:
        raise ValueError('feedback requires the unchanged bounded development schedule')
    scheduled=[(version,task) for version in ('baseline','candidate') for task in manifest['tasks']]
    if len(scheduled)!=len(evaluated['trials']):raise ValueError('feedback trial coverage differs from actual schedule')
    actual=[];joins=[];used=set();grading_status=evaluator().grading_status
    if evaluated.get('inputs_unchanged') is not True:raise ValueError('unchanged native trial inputs required')
    for index,(trial,(version,task)) in enumerate(zip(evaluated['trials'],scheduled)):
        if (trial.get('version')!=version or trial.get('task')!=task['id'] or trial.get('split')!=task['split']
                or trial.get('repetition')!=0):raise ValueError('feedback task differs from actual frozen schedule')
        if trial.get('split') not in ('development','regression'):continue
        execution=trial.get('execution');grade=trial.get('grader',{}).get('execution')
        request=execution.get('inference',{}).get('request_binding') if isinstance(execution,dict) else None
        requests=[row for row in observed.get('instruction_requests',[]) if request is not None
            and row['binding']['scope_id']==request.get('scope_id')]
        workers=[event for event in events if event['role']=='executor' and request is not None
            and event.get('instruction_scope')==request.get('scope_id')]
        if (not requests or any(not row['complete'] for row in requests) or requests[-1]['binding']!=request
                or request['role']!='executor' or request['trial_id']!='trial-'+str(index+1)
                or not workers or [pin(command) for command in execution.get('worker_commands',[])]
                    !=[event.get('command_sha256') for event in workers]
                or any(event['input_digests']!={'harness':binding[version+'_sha256']} for event in workers)):
            raise ValueError('feedback lacks actual owned executor/grader completion')
        worker=workers[-1]
        graders=[event for event in events if event['role']=='grader' and event['result_sha256']==pin(grade)
            and any(pair['executor']==worker['dispatch_sha256'] and pair['grader']==event['dispatch_sha256'] for pair in observed['pairs'])]
        if len(graders)!=1:raise ValueError('feedback lacks an exact actual artifact-grade pair')
        grader=graders[0];selected={event['dispatch_sha256'] for event in [*workers,grader]}
        oracle=str(contained(Path(binding['manifest']).parent,manifest['grader_bundles'][task['id']]['path']))
        typed=dict(grade,timeout=grade.get('status')=='timeout');status=grading_status(typed,typed=True)
        if (used&selected or grader['input_digests'].get('oracle')!=binding['oracle_roots'][oracle]
                or execution.get('status')!='completed' or execution.get('exit_code')!=0 or execution.get('inputs_unchanged') is not True
                or status not in ('passed','failed') or trial.get('status')!=status or trial['grader'].get('status')!=status
                or trial['grader'].get('outcome') is not typed.get('outcome')
                or trial['grader'].get('grader_identity')!={name:typed.get(name) for name in ('grader_id','grader_version')}):
            raise ValueError('feedback status, oracle or distinct owned trial differs')
        used.update(selected)
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


def application_guard(handle,grant):
    """Revalidate one exact authorized Journal checkpoint, never renew authority."""
    from agent_harness_lab.conditional_application import _bank_guard
    from .working_state import Snapshot
    value=_registered(handle)
    expected=_bank_guard(grant,handle)
    snapshot=expected['expected_current_project_snapshot']
    if (type(snapshot) is not Snapshot or expected['original_project_sha256']!=value['declaration']['project_sha256']
            or project_snapshot(value['project'],value['declaration']['project_inputs'],value['deadline'])!=snapshot):
        raise ValueError('application checkpoint differs from the exact owned Journal transition')
    _resources(value)
    from .bank_requalification import _application_current
    _application_current(value,expected)
    check_deadline(value['deadline'])
    return dict(status='current',profile='conditional-bank-requalification-v2',publication_allowed=False)
