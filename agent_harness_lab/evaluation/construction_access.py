"""Same-store canonical construction exposure; no scientific qualification issuer."""
from copy import deepcopy, copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import secrets
from weakref import WeakKeyDictionary, ref

from agent_harness_lab.private_journal import encoded, record, sync_directory, write_bytes
from . import certification_access as ledger
from .derivations import _check_role_custody, _hash, _tree_pin, _snapshot_pin, _rebased_output
from .decision_receipts import engine
from .integrity import contained, copy_frozen, digest
from .working_state import WorkingState, analysis_snapshot, check_deadline
from .analysis_inputs import UNLIMITED

PROFILE=ledger.CONSTRUCTION_PROFILE
_SCOPES=WeakKeyDictionary()
_STAGES=WeakKeyDictionary()


def _input_pin(path,deadline,analysis=False):
    return _snapshot_pin(analysis_snapshot(path,deadline)) if analysis else _tree_pin(path,deadline)


class _Stage:
    pass


def _disjoint(path, inputs):
    for source in inputs:
        source=ledger._path(source)
        if path.is_relative_to(source) or source.is_relative_to(path):
            raise ValueError('evaluation store must remain outside every role, bank and runtime input/output')


def selection(args, output=None):
    value=getattr(args,'evaluation_store',None)
    if value is None: return None
    if args.agent!='responses' or getattr(args,'repair_contracts',False):
        raise ValueError('evaluation store requires controlled Responses improvement, separate from contract repair')
    if any(getattr(args,key,None) is None for key in ('cgroup_root','image_root','image_sha256','disk_bytes')):
        raise ValueError('evaluation store requires the existing complete controlled resource profile')
    from agent_harness_lab import config
    from .responses import read_settings
    path=ledger._path(value)
    from agent_harness_lab.cli import assets
    bank=config.bank_path(args.project,args.project_settings) if getattr(args,'project_settings',None) else config.starter()
    inputs=[args.project,bank.parent,assets()/'skills']
    inputs.extend(value for value in (output or getattr(args,'output',None),getattr(args,'image_root',None),getattr(args,'inference_settings',None),
        *[getattr(args,name,None) for name in ('events','source_root','source_cache')]) if value is not None)
    _disjoint(path,inputs)
    if getattr(args,'inference_settings',None) is None: raise ValueError('evaluation store requires explicit inference settings')
    settings,_=read_settings(args.inference_settings)
    _disjoint(path,[settings['credential_file']])
    if getattr(args,'experiment_request',None) is not None:
        from .repeated_workflow import selected_store
        selected,pin=selected_store(args,output or getattr(args,'output',None))
        if selected!=path:raise ValueError('repeated and canonical stores differ')
        args._evaluation_request_sha256=pin
    from .oracle_history import HistoryStore
    store=HistoryStore(path)
    with store.locked(create=True):
        with ledger._locked(store.access_ledger().path): pass
    return path


def store_identity(path):
    from .oracle_history import HistoryStore
    store=HistoryStore(path)
    with store.locked(create=True):
        info=store.root.stat()
        return _hash([str(store.root),info.st_dev,info.st_ino])


def current_store_identity(path):
    """Read an initialized store's identity without acquiring its writer lock."""
    from .oracle_history import HistoryStore
    from .decision_receipts import private
    store=HistoryStore(ledger._path(path))
    private(store.root,directory=True)
    for name in ('.store.json','.lock'):private(store.root/name)
    marker,_=record(store.root/'.store.json',4096)
    if marker!={'schema_version':1,'format':store.format} or type(marker.get('schema_version')) is not int:
        raise ValueError('unsupported controller case store')
    info=store.root.stat()
    return _hash([str(store.root),info.st_dev,info.st_ino])


def _resources(args):
    result={key:str(value) if isinstance(value,Path) else value for key in
        ('timeout','trial_limit','cgroup_root','image_root','image_sha256','disk_bytes','permission_audit')
        for value in [getattr(args,key,None)]}
    if getattr(args,'experiment_request',None) is not None:
        result.update(experiment_request=str(ledger._path(args.experiment_request)),
            experiment_request_sha256=getattr(args,'_evaluation_request_sha256',None))
    return result


def _backend_options(backend):
    values=vars(backend)
    result={key:str(values[key]) if isinstance(values[key],Path) else values[key]
        for key in ('binary','cgroup_root','disk_bytes','permission_audit') if key in values}
    image=values.get('image')
    if image is not None: result['image']={'root':str(image.root),'sha256':image.sha256}
    return result


def _scope(handle):
    if type(handle) is not WorkflowAccess or handle not in _SCOPES:
        raise ValueError('actual canonical workflow ownership required')
    value=_SCOPES[handle]
    if value['pid']!=os.getpid(): raise ValueError('canonical workflow cannot continue in a different process')
    check_deadline(value['deadline'])
    args=value['args']
    from agent_harness_lab.one_call import check_configuration
    if (selection(args,value['output'])!=value['store'].root or store_identity(value['store'].root)!=value['store_identity']
            or getattr(args,'configuration_sha256',None)!=value['configuration_sha256']
            or _hash(getattr(args,'project_settings',None))!=_hash(value['project_settings'])
            or _hash(_resources(args))!=value['resource_pin'] or _hash(_backend_options(value['backend']))!=value['backend_pin']):
        raise ValueError('canonical workflow configuration/store changed')
    check_configuration(args)
    if (record(value['settings_path'],65536)[1]!=value['settings_pin']
            or value['client'].settings!=value['settings'] or _hash(engine())!=value['engine_pin']):
        raise ValueError('canonical workflow settings/engine changed')
    for owned in value['stages']:
        if not owned.get('published'):continue
        if WorkingState(owned['destination'],value['deadline']).snapshot!=owned['consumer_snapshot']:
            raise ValueError('published canonical output differs from owned consumer state')
    return value


def _check(owned, *, pending=True):
    _scope(owned['scope']())
    _check_role_custody(owned)
    for name,key in (('baseline','baseline_tree_sha256'),('context','context_tree_sha256')):
        if _input_pin(owned['folder']/name,owned['deadline'],owned.get('analysis',False) and name=='context')!=owned['value'][key]:
            raise ValueError('frozen canonical input snapshot changed')
    if owned['role']=='proposer' and _tree_pin(owned['folder']/'candidate',owned['deadline'])!=owned['value']['candidate_tree_sha256']:
        raise ValueError('frozen proposal input snapshot changed')
    for path,pin in owned['originals'].items():
        if _input_pin(path,owned['deadline'],owned.get('analysis',False) and path==owned['original_context'])!=pin: raise ValueError('original canonical input changed')
    entry=ledger.construction_record(owned['store'].access_ledger().path,owned['ident'])
    if (entry['input_binding_sha256']!=_hash(owned['inputs']) or entry['role']!=owned.get('ledger_role',owned['role'])
            or entry['parent_assets']!=owned['inputs']['parent_assets']
            or entry['parent_construction_id']!=owned['inputs']['parent_construction_id']
            or (pending and entry['state']!='pending')):
        raise ValueError('canonical stage differs from actual controller custody')
    if not pending:
        output,pin=record(owned['folder']/'output.json',ledger.MAX_BYTES)
        if (entry['state']!='complete' or pin!=entry['output_binding_sha256']
                or output!=owned['output'] or output['assets']!=entry['children']
                or _tree_pin(owned['folder']/'published',owned['deadline'])!=output['tree_sha256']):
            raise ValueError('completed canonical stage changed')
    return entry


def validate_role(handle,role,client,backend,baseline,context,workspace,destination,*,analysis=False):
    if type(handle) is not _Stage or handle not in _STAGES:
        raise ValueError('actual one-use canonical role handoff required')
    owned=_STAGES[handle]
    if owned.get('analysis',False)!=analysis:raise ValueError('canonical analysis input selection changed')
    if (owned['used'] or owned['role']!=role or owned['client'] is not client or owned['backend'] is not backend
            or Path(baseline)!=owned['baseline'] or Path(context)!=owned['context']
            or Path(workspace)!=owned['active']/'work' or Path(destination)!=owned['active']/'published'):
        raise ValueError('canonical role handoff identity changed or was copied')
    _check(owned)
    if role=='proposer' and _tree_pin(workspace,owned['deadline'])!=owned['seed_pin']:
        raise ValueError('canonical proposal seed changed')
    owned['used']=True


def record_role_output(handle,published):
    if type(handle) is not _Stage or handle not in _STAGES: raise ValueError('actual canonical role output owner required')
    owned=_STAGES[handle]; _check(owned)
    if not owned['used'] or 'produced_pin' in owned or Path(published)!=owned['active']/'published':
        raise ValueError('actual canonical output path required')
    owned['produced_state']=WorkingState(published,owned['deadline'])
    owned['produced_pin']=_snapshot_pin(owned['produced_state'].snapshot)


class WorkflowAccess:
    def __init__(self,args,output,baseline,client,backend,deadline):
        from .oracle_history import HistoryStore
        path=selection(args,output)
        if path is None: raise ValueError('explicit evaluation store required')
        check_deadline(deadline)
        settings,settings_pin=record(args.inference_settings,65536)
        implementation=engine()
        store=HistoryStore(path)
        with store.locked(create=True): pass
        _SCOPES[self]=dict(args=args,output=ledger._path(output),baseline=ledger._path(baseline),
            store=store,client=client,backend=backend,deadline=deadline,pid=os.getpid(),workflow_id=secrets.token_hex(32),
            configuration_sha256=getattr(args,'configuration_sha256',None),project_settings=deepcopy(getattr(args,'project_settings',None)),
            resources=_resources(args),resource_pin=_hash(_resources(args)),backend_pin=_hash(_backend_options(backend)),
            store_identity=store_identity(path),settings_path=ledger._path(args.inference_settings),settings_pin=settings_pin,settings=deepcopy(client.settings),
            engine_pin=_hash(implementation),implementation=implementation,stages=[],attempts=[],feedback=None,last_author=None,repairs=[],ignored=[])
        if settings!=client.settings: raise ValueError('workflow client differs from actual frozen settings')
        _scope(self)
        closed=getattr(args,'_closed_input_domain',None)
        if closed is not None:
            from .closed_inputs import attach,owned
            domain=owned(closed)
            if domain['history'].root!=store.root:raise ValueError('closed domain and canonical history differ')
            attach(closed,self)
            _SCOPES[self]['closed_domain']=closed

    def _run(self,role,context,destination,*,candidate=None,focus=None,analysis=False,bank_conversion=None):
        if type(analysis) is not bool:raise ValueError('explicit analysis input selection required')
        if bank_conversion is not None and (role!='evaluator_author' or type(bank_conversion) is not dict
                or set(bank_conversion)!={'metadata','controls_manifest','output','review_store','declaration'}):
            raise ValueError('exact author bank conversion inputs required')
        from . import construction
        data=_scope(self); deadline=data['deadline']; store=data['store']
        if data.get('final_author_started'):raise ValueError('final author scope cannot dispatch further roles')
        if bank_conversion is not None:
            from agent_harness_lab.promotion.prerequisites import current as current_scope
            if current_scope() is None or data['backend'].permission_audit is None:
                raise ValueError('author conversion requires a live native scope and permission audit')
        context=ledger._path(context); destination=ledger._path(destination)
        paths=[data['baseline'],context,*([ledger._path(candidate)] if candidate is not None else [])]
        _disjoint(store.root,[*paths,destination])
        if destination.exists(): raise ValueError('new canonical publication destination required')
        originals={path:_input_pin(path,deadline,analysis and path==context) for path in paths}
        parent=None; parents=[]
        if role=='proposer' and data['feedback'] is not None:
            author,rows=data['feedback']; _check(author,pending=False)
            actual,_=record(context/'feedback.json',ledger.MAX_BYTES)
            if actual!={'trials':rows}: raise ValueError('feedback differs from actual completed author handoff')
            source,_=record(author['folder']/'published/manifest.json',ledger.MAX_BYTES)
            selected={row['task'] for row in rows}
            tasks=[t for t in source['tasks'] if t['id'] in selected]
            parents,_=ledger.exposure(author['folder']/'published',tasks,source['grader_bundles'],lambda:check_deadline(deadline))
            parent=author['ident']
        settings_snapshot=dict(profile=PROFILE,source_sha256=data['settings_pin'],settings=data['settings'])
        inputs=dict(schema_version=1,profile=PROFILE,workflow_id=data['workflow_id'],role=role,
            resources=data['resources'],backend_selection_sha256=data['backend_pin'],store_identity_sha256=data['store_identity'],configuration_sha256=data['configuration_sha256'],originals={str(k):v for k,v in originals.items()},
            parent_construction_id=parent,parent_assets=parents,scientific_parentage='unknown' if parent is None else 'controller_available_feedback',
            settings_sha256=data['settings_pin'],settings_snapshot_sha256=_hash(settings_snapshot),engine_sha256=data['engine_pin'])
        if analysis:inputs['analysis_fixture_admission']='unlimited_readonly_v1'
        closed=data.get('closed_domain')
        if closed is not None:
            from .closed_inputs import stage_input
            inputs['closed_input']=stage_input(closed,self,role,context,parent=parent,
                feedback=data['feedback'][1] if parent is not None else None)
        _scope(self)
        ledger_role='final_bank_author' if bank_conversion is not None else role
        if bank_conversion is not None:data['final_author_started']=True
        ident=ledger.begin_construction(store.access_ledger().path,ledger_role,_hash(inputs),parent=parent,parents=parents)
        data['attempts'].append((ident,role,_hash(inputs)))
        folder=contained(store.root,'constructions/'+ident)
        active=contained(data['output'],'.construction-roles/'+ident)
        completed=False; issuer=os.getpid(); handle=None; capture=None
        try:
            with store.locked():
                folder.parent.mkdir(mode=0o700,exist_ok=True); folder.mkdir(mode=0o700)
            active.parent.mkdir(mode=0o700,exist_ok=True); active.mkdir(mode=0o700)
            for name,value in (('inputs',inputs),('engine',data['implementation']),('settings',settings_snapshot)):
                write_bytes(folder/(name+'.json'),encoded(value))
            for original,target in ((data['baseline'],'baseline'),(context,'context')):
                limits=UNLIMITED if analysis and target=='context' else {}
                copy_frozen(original,folder/target,digest(original,**limits),**limits)
                if _input_pin(folder/target,deadline,analysis and target=='context')!=originals[original]: raise ValueError('canonical input changed while copying')
                copy_frozen(folder/target,active/target,digest(folder/target,**limits),**limits)
                if _input_pin(active/target,deadline,analysis and target=='context')!=originals[original]: raise ValueError('active canonical input changed while copying')
            seed_pin=None
            if role=='proposer':
                copy_frozen(candidate,folder/'candidate',digest(candidate))
                if _tree_pin(folder/'candidate',deadline)!=originals[ledger._path(candidate)]: raise ValueError('candidate changed while copying')
                construction.proposer_seed(folder/'candidate',active/'work',deadline)
                seed_pin=_tree_pin(active/'work',deadline)
            value=dict(schema_version=1,profile=PROFILE,construction_id=ident,
                context_tree_sha256=_input_pin(active/'context',deadline,analysis),baseline_tree_sha256=_tree_pin(active/'baseline',deadline),engine_sha256=data['engine_pin'])
            if role=='proposer':
                value.update(candidate_tree_sha256=originals[ledger._path(candidate)],seed_tree_sha256=seed_pin)
            write_bytes(folder/'handoff.json',encoded(value))
            owned=dict(scope=ref(self),store=store,ident=ident,folder=folder,active=active,role=role,ledger_role=ledger_role,pid=issuer,used=False,
                baseline=active/'baseline',context=active/'context',original_context=context,analysis=analysis,deadline=deadline,value=value,inputs=inputs,
                client=data['client'],backend=data['backend'],settings=data['settings'],settings_path=data['settings_path'],originals=originals,seed_pin=seed_pin)
            handle=_Stage(); _STAGES[handle]=owned
            args=(data['client'],data['backend'],owned['baseline'],owned['context'],active/'work',active/'published',deadline)
            capture=None
            if closed is not None or bank_conversion is not None:
                from .construction_seals import begin
                if closed is not None:
                    from .closed_inputs import owned as closed_owned
                    stage_scope=closed_owned(closed)['scope']
                else:
                    stage_scope=current_scope()
                capture=begin(stage_scope,data['client'],data['backend'],role,
                    owned['baseline'],owned['context'],deadline)
            if role=='proposer':
                _,_,commands=construction.propose(*args,focus=focus,exposure=handle,**({'analysis':True} if analysis else {}))
            else:
                options={'analysis':True} if analysis else {}
                if bank_conversion is not None:options['bank_qualification']=True
                made=[]; dropped=[]; options['ignored']=dropped
                if bank_conversion is None:options.update(repairs=made,repair_allowed=closed is None)  # sealed closed-input stages allow no second pass
                try:_,commands=construction.author(*args,derivation=handle,**options)
                finally:data['repairs'].extend(made); data['ignored'].extend(dropped)
            if not owned['used']: raise ValueError('canonical role did not consume its actual handoff')
            _check(owned)
            if owned.get('produced_pin')!=_tree_pin(active/'published',deadline):
                raise ValueError('actual role output changed after completion')
            produced=owned['produced_state']; original=produced.snapshot; expected=original
            seal=None
            if capture is not None:
                from .construction_seals import finish,read
                seal=finish(capture,original,destination=active/'published',drop_digests=bank_conversion is None)
                sealed=read(seal,stage_scope)
            children=[]; references=None
            if role=='evaluator_author':
                path=active/'published/manifest.json'; source,manifest_pin=record(path,ledger.MAX_BYTES)
                original_manifest=next(raw for name,_,raw in original.files if name=='manifest.json')
                if manifest_pin!=hashlib.sha256(original_manifest).hexdigest():
                    raise ValueError('actual canonical manifest changed during capture')
                if not 3<=len(source['tasks'])<=5: raise ValueError('canonical author requires the existing bounded task count')
                if bank_conversion is not None and any(task.get('split')!='heldout' for task in source['tasks']):
                    raise ValueError('final bank author may produce only held-out tasks')
                check=lambda:check_deadline(deadline)
                expected_capture=ledger.snapshot_exposure(original,source['tasks'],source['grader_bundles'],check)
                captured=ledger.exposure(path.parent,source['tasks'],source['grader_bundles'],check)
                references=ledger.covered_oracle_references(path.parent,source['tasks'],source['grader_bundles'],check)
                if captured!=expected_capture or _tree_pin(path.parent,deadline)!=owned['produced_pin']:
                    raise ValueError('actual canonical output changed during asset capture')
                children,_=captured
                for task in source['tasks']:
                    task['grader']=['/usr/bin/python3',str(destination/source['grader_bundles'][task['id']]['path']/'grade.py.txt'),'{workspace}',
                        str(destination/source['grader_bundles'][task['id']]['path']/'expected.json')]
                source['controller_derivation']=dict(profile=PROFILE,construction_id=ident,scientific_parentage='unknown',qualification='unverified',publication_allowed=False)
                if _tree_pin(path.parent,deadline)!=owned['produced_pin']:
                    raise ValueError('actual canonical output changed before manifest rebasing')
                expected=_rebased_output(original,encoded(source))
            produced.snapshot=expected
            produced.publish(folder/'published',deadline)
            if WorkingState(folder/'published',deadline).snapshot!=expected:
                raise ValueError('actual canonical output changed during frozen publication copy')
            produced.snapshot=expected
            output=dict(schema_version=1,profile=PROFILE,construction_id=ident,input_binding_sha256=_hash(inputs),
                handoff_sha256=_hash(value),tree_sha256=_snapshot_pin(expected),assets=children)
            if seal is not None:output['closed_input']=dict(proof=sealed,producer_tree_sha256=_snapshot_pin(original))
            write_bytes(folder/'output.json',encoded(output)); sync_directory(folder)
            _check(owned)
            if (_tree_pin(folder/'published',deadline)!=output['tree_sha256']
                    or record(folder/'output.json',ledger.MAX_BYTES)[1]!=_hash(output)):
                raise ValueError('canonical output changed before registration')
            ledger.finish_construction(store.access_ledger().path,ident,_hash(inputs),children,_hash(output),references=references); completed=True
            owned['output']=output; owned['destination']=destination; owned['consumer_snapshot']=expected
            data['stages'].append(owned)
            if closed is not None:
                from .closed_inputs import add_stage
                add_stage(closed,self,ident,seal,output)
            if role=='evaluator_author' and bank_conversion is None: data['last_author']=owned
            produced.publish(destination,deadline); owned['published']=True
            _check(owned,pending=False)
            if role=='proposer':
                proposal,pin=record(destination/'proposal.json',65536)
                raw=next(raw for name,_,raw in expected.files if name=='proposal.json')
                if pin!=hashlib.sha256(raw).hexdigest():raise ValueError('published proposal differs from actual producer')
                _scope(self)
                return proposal,destination/'candidate',commands
            if bank_conversion is not None:
                if seal is None:raise ValueError('live sealed author boundary required for bank conversion')
                from .author_bank_conversion import convert
                converted=convert(handle,seal,stage_scope,**bank_conversion)
                return destination/'manifest.json',commands,converted
            return destination/'manifest.json',commands
        except BaseException:
            if capture is not None:
                from .construction_seals import cancel
                cancel(capture)
            if not completed and os.getpid()==issuer:
                ledger.finish_construction(store.access_ledger().path,ident,_hash(inputs))
            raise
        finally:
            if handle is not None: _STAGES.pop(handle,None)

    def propose(self,context,candidate,destination,*,focus=None,analysis=False):
        if (focus is not None and type(self) is WorkflowAccess and self in _SCOPES
                and _SCOPES[self].get('closed_domain') is not None):
            raise ValueError('focus is unsupported by the closed input profile')
        return self._run('proposer',context,destination,candidate=candidate,focus=focus,analysis=analysis)

    def author(self,context,destination,*,analysis=False,bank_conversion=None):
        return self._run('evaluator_author',context,destination,analysis=analysis,bank_conversion=bank_conversion)

    def ignored_digests(self):
        return sum(_scope(self)['ignored'])

    def repair_log(self):
        return deepcopy(_scope(self)['repairs'])

    def freeze_candidate(self,candidate,destination):
        data=_scope(self)
        owned=next((item for item in reversed(data['stages'])
            if item['role']=='proposer' and item['destination']/'candidate'==Path(candidate)),None)
        if owned is None:raise ValueError('actual proposer candidate required')
        snapshot=owned['consumer_snapshot']; modes=dict(snapshot.directories)
        expected=replace(snapshot,root_mode=modes['candidate'],
            directories=tuple((name[len('candidate/'):],mode) for name,mode in snapshot.directories if name.startswith('candidate/')),
            files=tuple((name[len('candidate/'):],mode,raw) for name,mode,raw in snapshot.files if name.startswith('candidate/')))
        state=owned['produced_state']; selected=copy(state); selected.snapshot=expected
        selected.publish(destination,data['deadline'])
        if WorkingState(destination,data['deadline']).snapshot!=expected:raise ValueError('frozen candidate differs from producer')
        if data.get('closed_domain') is not None:
            from .closed_inputs import frozen_candidate
            frozen_candidate(data['closed_domain'],self,destination,expected,owned['ident'])
        return expected

    def validate_manifest(self,path,*args,**kwargs):
        from agent_harness_lab.workflow import validate_manifest
        data=_scope(self)
        owned=next((item for item in reversed(data['stages'])
            if item['role']=='evaluator_author' and item['destination']/'manifest.json'==Path(path)),None)
        if owned is None:raise ValueError('actual author manifest required')
        original=owned['consumer_snapshot']
        result=validate_manifest(path,*args,**kwargs,expected_snapshot=original,expected_deadline=data['deadline'])
        if result is not None:
            owned['consumer_snapshot']=_rebased_output(original,(json.dumps(result,indent=2,allow_nan=False)+'\n').encode(),manifest_mode=0o600)
            _scope(self)
        return result

    def feedback(self,rows):
        data=_scope(self); author=data['last_author']
        if data.get('final_author_started'):raise ValueError('final author scope cannot issue feedback')
        if author is None: raise ValueError('feedback requires actual same-workflow author custody')
        _check(author,pending=False)
        source,_=record(author['folder']/'published/manifest.json',ledger.MAX_BYTES)
        indexed={task['id']:task for task in source['tasks']}
        if not isinstance(rows,list) or not 0<len(rows)<=2000: raise ValueError('bounded actual feedback required')
        for row in rows:
            if (not isinstance(row,dict) or set(row)!={'task','version','split','status'} or row['task'] not in indexed
                    or row['split'] not in ('development','regression') or row['split']!=indexed[row['task']]['split']
                    or row['version'] not in ('baseline','candidate') or not isinstance(row['status'],str) or not 0<len(row['status'])<=128):
                raise ValueError('feedback task differs from actual completed construction')
        data['feedback']=(author,deepcopy(rows))

    def report(self):
        data=_scope(self)
        stages=[]
        for owned in data['stages']:
            entry=_check(owned,pending=False)
            stages.append(dict(construction_id=owned['ident'],role=entry['role'],state=entry['state'],
                parent_construction_id=entry['parent_construction_id'],scientific_parentage=owned['inputs']['scientific_parentage'],
                input_binding_sha256=entry['input_binding_sha256'],output_binding_sha256=entry['output_binding_sha256'],
                consumer_tree_sha256=_snapshot_pin(owned['consumer_snapshot'])))
        return dict(schema_version=1,profile=PROFILE,workflow_id=data['workflow_id'],stages=stages,
            qualification='unverified',scientific_independence='unverified',native_identity='unverified',
            automatic_promotion=False,publication_allowed=False)

    def retire(self):
        data=_SCOPES.get(self)
        if data is None: return
        if data['pid']!=os.getpid(): raise ValueError('actual workflow issuer required for retirement')
        _SCOPES.pop(self)
        if getattr(data['args'],'_construction_access',None) is self:
            del data['args']._construction_access
        data.clear()

    def failure_report(self):
        if type(self) is not WorkflowAccess or self not in _SCOPES or _SCOPES[self]['pid']!=os.getpid():
            raise ValueError('actual workflow issuer required for failure custody')
        data=_SCOPES[self]; stages=[]
        for ident,role,pin in data['attempts']:
            entry=ledger.construction_record(data['store'].access_ledger().path,ident)
            if entry['input_binding_sha256']!=pin or entry['role']!=role: raise ValueError('failure custody changed')
            stages.append(dict(construction_id=ident,role=role,state=entry['state']))
        return dict(schema_version=1,profile=PROFILE,workflow_id=data['workflow_id'],stages=stages,
            applicability='unknown',qualification='unverified',recovery='requires_intervention',
            automatic_promotion=False,publication_allowed=False)
