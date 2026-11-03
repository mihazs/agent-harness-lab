"""One-use fresh comparison custody; persisted observations are never live capabilities."""
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
import stat
import os
import time
from weakref import WeakKeyDictionary

from agent_harness_lab.private_journal import encoded
from .integrity import digest
from .plan import schedule

PROFILE='ahl-fresh-responses-comparison-v1'
_ACTIVE=ContextVar('ahl_fresh_comparison',default=None)
_STATES=WeakKeyDictionary()
_FRESH=WeakKeyDictionary()
MAX_BYTES=8*1024*1024


def hashed(value):return hashlib.sha256(encoded(value)).hexdigest()


def current():return _ACTIVE.get()


def snapshot_pin(snapshot):
    return hashed(dict(root_mode=snapshot.root_mode,directories=snapshot.directories,
        files=[(name,mode,hashlib.sha256(raw).hexdigest()) for name,mode,raw in snapshot.files]))


def bundle_binding(root,deadline):
    from pathlib import Path
    from .integrity import tree
    from .working_state import check_deadline
    root=Path(root);check_deadline(deadline)
    files=tree(root,max_files=1000,max_bytes=10*1024*1024)
    directories={'.':stat.S_IMODE(root.stat().st_mode)}
    for index,path in enumerate(root.rglob('*')):
        check_deadline(deadline)
        if index>=5000 or len(path.relative_to(root).parts)>64:raise ValueError('bundle inventory bound exceeded')
        info=path.lstat()
        if stat.S_ISDIR(info.st_mode):directories[str(path.relative_to(root))]=stat.S_IMODE(info.st_mode)
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('bundle inventory contains an unsafe entry')
    if tree(root,max_files=1000,max_bytes=10*1024*1024)!=files:raise ValueError('bundle changed during capture')
    check_deadline(deadline)
    return dict(file_tree_sha256=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),
        bundle_sha256=hashed(dict(files=files,directories=directories)))


def fixture_seed(path,deadline):
    from .working_state import WorkingState
    return snapshot_pin(WorkingState(path,deadline).snapshot)


class Comparison:
    def __init__(self,spec,fixtures,client,deadline,trajectories=None,seeds=None,*,permission_profile=None,controls=False,bundles=None,journal_binding=None):
        from .responses import Client
        if type(client) is not Client or _ACTIVE.get() is not None or spec.sequential is not None:
            raise ValueError('one fresh fixed Responses comparison required')
        if permission_profile not in (None,'permission-results-v1') or type(controls) is not bool:
            raise ValueError('supported expected permission profile required')
        self.permission_profile,self.controls=permission_profile,controls
        self.bundles=deepcopy(bundles or {})
        self.journal_binding=deepcopy(journal_binding)
        self.spec,self.fixtures,self.client,self.deadline=spec,dict(fixtures),client,deadline
        self.trajectories=trajectories or {};self.seeds=seeds or {}
        self.settings_pin=hashed(client.settings)
        self.expected=schedule(spec);self.rows=[];self.active=None;self.pending=[]
        self.latest=None;self.lost=False;self.closed=False;self.token=None
        _FRESH[self]=(os.getpid(),self._state())

    def _state(self):
        return encoded(dict(spec=self.spec.digest,fixtures=self.fixtures,client=id(self.client),settings_pin=self.settings_pin,deadline=self.deadline,
            permission_profile=self.permission_profile,controls=self.controls,bundles=self.bundles,journal_binding=self.journal_binding,trajectories=self.trajectories,seeds=self.seeds,expected=self.expected,rows=self.rows,active=self.active,pending=self.pending,latest=None if self.latest is None else id(self.latest),
            lost=self.lost,closed=self.closed))

    def _save(self):
        owner=_STATES.get(self)
        if owner is None or owner[0]!=os.getpid():self.lost=True;return
        raw=self._state()
        if len(raw)>MAX_BYTES:self.lost=True;return
        _STATES[self]=(owner[0],owner[1],raw)

    def _valid(self):
        value=_STATES.get(self)
        valid=(type(self) is Comparison and current() is self and not self.closed and not self.lost
            and value is not None and value[0]==os.getpid() and value[2]==self._state()
            and hashed(self.client.settings)==self.settings_pin and time.monotonic()<self.deadline)
        if not valid:
            self.lost=True
            if value is not None:_STATES[self]=(value[0],value[1],None)
        return valid

    def __enter__(self):
        fresh=_FRESH.pop(self,None)
        if (fresh is None or fresh[0]!=os.getpid() or current() is not None or self.closed
                or time.monotonic()>=self.deadline or fresh[1]!=self._state()):
            raise ValueError('fresh live comparison admission required')
        token=_ACTIVE.set(self)
        try:_STATES[self]=(os.getpid(),token,self._state())
        except BaseException:
            _STATES.pop(self,None);_ACTIVE.reset(token);raise
        return self

    def __exit__(self,*errors):
        owner=_STATES.pop(self,None)
        try:
            if owner is not None and owner[0]==os.getpid() and current() is self:_ACTIVE.reset(owner[1])
        finally:
            if current() is self:_ACTIVE.set(None)
            self.closed=True;self.latest=None

    def before(self,scheduled,workspace,harness,client):
        if (not self._valid() or self.controls or self.active is not None or client is not self.client
                or len(self.rows)>=len(self.expected) or scheduled!=self.expected[len(self.rows)]):
            raise ValueError('fresh comparison trial admission changed')
        if digest(workspace)!=self.fixtures[scheduled['task']]:
            raise ValueError('seeded comparison fixture differs from frozen fixture')
        expected=self.spec.baseline_hash if scheduled['version']=='baseline' else self.spec.candidate_hash
        from agent_harness_lab.cli import evaluator
        if evaluator().digest(harness)!=expected:raise ValueError('comparison harness changed')
        frozen=self.bundles[scheduled['version']]
        if bundle_binding(harness,self.deadline)!=frozen:raise ValueError('frozen mode-aware harness bundle changed')
        self.active=dict(scheduled=deepcopy(scheduled),harness=frozen['file_tree_sha256'],requests=[],workers=[],grade=None,
            request_start=client.requests,fixture_sha256=self.fixtures[scheduled['task']])
        self.pending=[];self.latest=None;self._save()

    def before_request(self,client):
        if (not self._valid() or self.controls or client is not self.client or self.active is None
                or any(not event['complete'] for event in self.pending)
                or self.active['requests'] and not self.active['workers']):
            raise ValueError('comparison custody unavailable before another model request')

    def request(self,client):
        if not self._valid() or self.active is None or client is not self.client:
            raise ValueError('owned comparison transport completion required')
        if client.pending or client.poisoned or not client.response_protocol_validated:
            raise ValueError('comparison response remains unresolved')
        if client.requests<=self.active['request_start'] or client.requests in self.active['requests']:
            raise ValueError('comparison response is not fresh')
        self.active['requests'].append(client.requests)
        if client.settings.get('instruction_protocol') is not None:
            from .responses import _read_request
            value=_read_request(client,complete=True)
            if value['trial_id']!=self.active['scheduled']['trial_id']:raise ValueError('instruction request trial changed')
            self.active.setdefault('instruction_requests',[]).append(value)
        self._save()

    def reservation(self,client,trial_id):
        self.before_request(client)
        selected = self.active['scheduled']
        if selected['trial_id'] != trial_id:
            raise ValueError('request differs from actual admitted trial')
        binding = dict(plan_sha256=self.spec.digest,schedule_sha256=hashed(self.expected),
            scheduled_trial_sha256=hashed(selected),trial_id=trial_id,
            repetition=selected['repetition'],version=selected['version'])
        if self.journal_binding is not None:binding['journal']=deepcopy(self.journal_binding)
        return binding

    def _admit(self,role,stage):
        if not self._valid() or self.active is None or stage!='role' or role not in ('executor','grader') or self.controls and role!='grader':
            self.lost=True;return None
        event=dict(role=role,stage=stage,complete=False)
        self.pending.append(event);self.latest=None;self._save()
        return event

    def _observe(self,ticket,fact,run):
        from .controller_receipts import _read_fact
        value=_read_fact(fact,self,ticket)
        event=ticket.comparison_event
        if not self._valid() or value is None or event is None or event is not self.pending[-1] or event['complete']:
            self.lost=True;return
        permission=value.get('permission')
        if ((self.permission_profile is None and permission is not None) or self.permission_profile is not None
                and (not isinstance(permission,dict) or permission.get('profile')!=self.permission_profile
                    or permission.get('qualification')!='qualified')):
            self.lost=True;return
        if value['image_sha256']!=self.spec.runtime_hash:
            self.lost=True;return
        if value['role']=='executor' and (value['input_digests']!={'harness':self.active['harness']}
                or value.get('input_bundles')!={'harness':self.bundles[self.active['scheduled']['version']]}
                or value.get('input_bundles_unchanged') is not True):
            self.lost=True;return
        event.update(value)
        if value['role']=='executor':
            expected_seed=(self.active['workers'][-1]['fact']['captured_state_sha256'] if self.active['workers'] else self.seeds.get(self.active['scheduled']['task']))
            if value.get('seed_state_sha256')!=expected_seed or not isinstance(value.get('captured_state_sha256'),str):
                self.lost=True;return
            if not self.active['requests']:
                self.lost=True;return
            from .controller_receipts import _worker_command
            self.active['workers'].append(dict(fact=value,command=_worker_command(run)))
        self.latest=run;self._save()

    def before_control(self,workspace,allowlist,oracle_sha256,expected):
        from .integrity import artifacts
        from .controller_receipts import _artifact_fingerprints
        if not self._valid() or not self.controls or self.active is not None:
            raise ValueError('current owned control admission required')
        self.active=dict(oracle=oracle_sha256,expected=expected,
            artifacts=hashed(_artifact_fingerprints(artifacts(workspace,allowlist).items())),grade=None)
        self.pending=[];self.latest=None;self._save()

    def after_control(self,grade):
        if (not self._valid() or not self.controls or self.active is None or self.active['grade'] is None
                or any(not event['complete'] for event in self.pending)
                or self.active['grade']['run_sha256']!=hashed(grade.get('execution'))
                or grade.get('outcome') is not self.active['expected']):
            raise ValueError('requested grader control lacks qualified owned completion')
        self.active=None;self.pending=[];self.latest=None;self._save()

    def _grade(self,run,extracted,oracle_sha256,fixture_sha256,outcome):
        from .controller_receipts import _matches_grade,_artifact_fingerprints
        if not self._valid() or self.active is None or self.latest is not run or not self.pending:
            self.lost=True;return
        event=self.pending[-1]
        if self.controls:
            if (not _matches_grade(run,event,oracle_sha256,fixture_sha256,outcome)
                    or oracle_sha256!=self.active['oracle'] or outcome is not self.active['expected']
                    or hashed(_artifact_fingerprints(extracted.items()))!=self.active['artifacts']):
                self.lost=True;return
            self.active['grade']=dict(run_sha256=hashed(run));self.latest=None;self._save();return
        if (not _matches_grade(run,event,oracle_sha256,fixture_sha256,outcome)
                or not self.active['workers'] or self.active['grade'] is not None):
            self.lost=True;return
        exported=self.active['workers'][-1]['fact']['exports']
        selected=_artifact_fingerprints(extracted.items())
        if not selected or any(list(pair) not in exported for pair in selected):
            self.lost=True;return
        self.active['grade']=dict(run_sha256=hashed(run),outcome=outcome,oracle_sha256=oracle_sha256,
            artifacts_sha256=hashed(selected),dispatch_sha256=event['dispatch_sha256'],fact=deepcopy(event))
        self.latest=None;self._save()

    def after(self,row):
        if (not self._valid() or self.active is None or any(not event['complete'] for event in self.pending)
                or not self.active['requests'] or not self.active['workers'] or self.active['grade'] is None):
            raise ValueError('comparison trial lacks fresh transport, worker or artifact-grade custody')
        if any(row.get(k)!=v for k,v in self.active['scheduled'].items()):
            raise ValueError('comparison trial identity changed')
        if self.client.settings.get('instruction_protocol') is not None:
            requests=self.active.get('instruction_requests',[])
            if ([value['request_ordinal'] for value in requests]!=self.active['requests']
                    or any(value['instruction_binding']['harness_bundle']!=self.bundles[self.active['scheduled']['version']] for value in requests)):
                raise ValueError('owned instruction request coverage or frozen bundle differs')
        execution=row.get('execution',{});grade=row.get('grader',{})
        binding=self.trajectories.get(row['task'])
        if binding is not None:
            from .trajectory_assertions import compose,evaluate
            composed=compose(binding,grade,evaluate(binding,self.client.actions.trajectory))
            if composed!=row.get('controller_grade') or composed['valid'] is not True or type(composed['outcome']) is not bool:
                raise ValueError('comparison trajectory is unknown or differs from the owned live channel')
        elif 'controller_grade' in row:raise ValueError('unexpected comparison trajectory grade')
        if row.get('status') not in ('passed','failed'):raise ValueError('comparison trial outcome is unknown')
        if (execution.get('worker_commands')!=[value['command'] for value in self.active['workers']]
                or hashed(grade.get('execution'))!=self.active['grade']['run_sha256']
                or grade.get('outcome') is not self.active['grade']['outcome']):
            raise ValueError('comparison results differ from actual owned completions')
        self.rows.append(dict(trial_sha256=hashed(row),custody=deepcopy(self.active)))
        self.active=None;self.pending=[];self.latest=None;self._save()

    def finish(self,result):
        if not self._valid() or self.controls or self.active is not None or len(self.rows)!=len(self.expected):
            raise ValueError('fresh comparison is incomplete')
        if self.client.pending or self.client.poisoned or self.client.requests!=self.rows[-1]['custody']['requests'][-1]:
            raise ValueError('comparison transport remained pending or changed outside admitted trials')
        if [value['trial_sha256'] for value in self.rows]!=[hashed(row) for row in result['trials']]:
            raise ValueError('fresh comparison final trial bytes changed')
        value=dict(profile=PROFILE,plan_sha256=self.spec.digest,result_sha256=hashed(result),
            permission_profile=self.permission_profile,bundles=self.bundles,fixtures=self.fixtures,seed_states=self.seeds,rows=deepcopy(self.rows),served_model='unverified',native_identity='unverified',
            independence='unverified',publication_allowed=False,adoption_authority=False)
        if self.client.settings.get('instruction_protocol') is not None:value['instruction_protocol']=self.client.settings['instruction_protocol']
        return value


def validate(value,spec,result):
    if (not isinstance(value,dict) or value.get('profile')!=PROFILE or value.get('plan_sha256')!=spec.digest
            or value.get('result_sha256')!=hashed(result)
            or len(value.get('rows',[]))!=len(schedule(spec))
            or [row.get('trial_sha256') for row in value['rows']]!=[hashed(row) for row in result['trials']]):
        raise ValueError('sealed fresh comparison binding changed')

    expected=value.get('permission_profile')
    if expected not in (None,'permission-results-v1'):raise ValueError('unsupported sealed permission profile')
    for row in value['rows']:
        custody=row['custody']
        frozen=value['bundles'][custody['scheduled']['version']]
        if value.get('instruction_protocol') is not None:
            from .instruction_context import PROFILE as INSTRUCTION_PROFILE
            requests=custody.get('instruction_requests',[])
            if (value['instruction_protocol']!=INSTRUCTION_PROFILE
                    or [request['request_ordinal'] for request in requests]!=custody['requests']
                    or any(request['instruction_binding']['profile']!=INSTRUCTION_PROFILE
                        or request['instruction_binding']['harness_bundle']!=frozen for request in requests)):
                raise ValueError('sealed instruction request coverage changed')
        if custody['harness']!=frozen['file_tree_sha256']:raise ValueError('sealed bundle inventory changed')
        for worker in custody['workers']:
            if worker['fact'].get('input_bundles')!={'harness':frozen} or worker['fact'].get('input_bundles_unchanged') is not True:
                raise ValueError('sealed native bundle inventory changed')
        facts=[worker['fact'] for worker in custody['workers']]+[custody['grade']['fact']]
        for fact in facts:
            permission=fact.get('permission')
            if (expected is None and permission is not None or expected is not None
                    and (not isinstance(permission,dict) or permission.get('profile')!=expected
                        or permission.get('qualification')!='qualified')):
                raise ValueError('sealed comparison lacks the requested qualified monitor')
