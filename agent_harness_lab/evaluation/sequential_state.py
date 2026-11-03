"""Owned online root sampling and immutable inspection custody; no cold execution resume."""
from pathlib import Path
import time
from threading import Lock

from agent_harness_lab.monitoring.state import atomic_json
from agent_harness_lab.private_journal import encoded, record
from . import sequential as math_core
from .integrity import contained
from .statistics import TrialRecord

_ISSUER=object()
MAX_LEDGER_BYTES=4*1024*1024


def records(spec,rows,policies=None):
    result=[]
    for row in rows:
        if not isinstance(row,dict):raise ValueError('sequential trial object required')
        grade=row.get('controller_grade',row.get('grader',{}))
        if not isinstance(grade,dict):raise ValueError('sequential grade object required')
        outcome=grade.get('outcome');critical=row.get('version')=='candidate' and grade.get('critical_violation') is True
        if policies and row.get('task') in policies:
            from .behavior import observation
            observed=observation(policies[row['task']],row.get('grader',{}).get('execution'))
            critical|=row.get('version')=='candidate' and observed['critical']
            if not observed['complete']:outcome=None
        if not critical and (row.get('status') not in ('passed','failed') or type(outcome) is not bool
                or row['status']!=('passed' if outcome else 'failed')
                or 'controller_grade' in row and grade.get('valid') is not True
                or row.get('isolation_qualification','qualified')!='qualified'):
            raise ValueError('sequential root has incomplete or unqualified grade')
        result.append(TrialRecord(row['trial_id'],outcome,'graded','executable_grader',
            spec.baseline_hash,spec.candidate_hash,spec.grader_hash,spec.runtime_hash,
            critical))
    return result


class Controller:
    def __init__(self,spec,path,deadline,*,inputs_sha256,cohort_json=None,checker=None,guard=None):
        self._lease=[Lock(),False]
        self._issuer=_ISSUER;self.spec=spec;self.path=Path(path);self.deadline=deadline
        self.cohort_json=cohort_json;self.checker=checker or (lambda rows:records(spec,rows));self.guard=guard
        self.binding=math_core.create_binding(spec,math_core.membership(spec,cohort_json),inputs_sha256=inputs_sha256)
        self.schedule=math_core.schedule(spec,self.binding);self.index=0;self.offset=0;self.active=False
        self.root_deadline=None;self.terminal=None;self.previous=math_core.hashed(self.binding)
        self.path.mkdir(mode=0o700);atomic_json(self.path/'binding.json',self.binding)
        self.files={'binding.json':record(self.path/'binding.json',1024*1024)[1]}
        self.last_analysis=None;self.owned_records=[];self.committed=[]

    def validate(self,spec,*,inputs_sha256=None):
        if inputs_sha256 is not None and self.binding['inputs_sha256']!=inputs_sha256:raise ValueError('sequential input view changed')
        if self._issuer is not _ISSUER or self.spec.digest!=spec.digest or self.terminal is not None:
            raise ValueError('live sequential controller required; cold resume unsupported')
        if record(self.path/'binding.json',1024*1024)[0]!=self.binding:raise ValueError('owned sampling binding changed')
        math_core.validate_binding(spec,self.binding)

    def claim(self,spec,inputs_sha256):
        with self._lease[0]:
            if self._lease[1]:raise ValueError('sequential controller is one-use; cold resume unsupported')
            self.validate(spec,inputs_sha256=inputs_sha256);self._lease[1]=True

    def _intact(self):
        if {p.name for p in self.path.iterdir()}!=set(self.files):raise ValueError('sequential ledger inventory changed')
        for name,pin in self.files.items():
            if record(contained(self.path,name),1024*1024)[1]!=pin:raise ValueError('sequential prefix bytes changed')

    def _write(self,name,value):
        self._intact()
        path=contained(self.path,name)
        if path.exists():raise ValueError('immutable sequential inspection already exists')
        if len(encoded(value))>256*1024:raise ValueError('sequential inspection byte bound')
        used=sum(p.stat().st_size for p in self.path.iterdir())
        if used+len(encoded(value))>MAX_LEDGER_BYTES:raise ValueError('sequential ledger byte bound')
        atomic_json(path,value)
        self.files[name]=record(path,1024*1024)[1]

    def _guard(self):
        self._intact()
        if self.guard is not None:self.guard()
        if record(self.path/'binding.json',1024*1024)[0]!=self.binding or math_core.engine_pin()!=self.binding['engine_sha256']:
            raise ValueError('sequential authority or engine changed')
        if time.monotonic()>=self.deadline:raise TimeoutError('sequential parent deadline exhausted')

    def stop(self,status,reason,rows):
        if self.terminal is not None:return
        self.terminal=dict(schema_version=1,status=status,reason=reason,completed_roots=self.index,
            trials_sha256=math_core.hashed(dict(trials=rows)),previous_sha256=self.previous,
            analysis_sha256=math_core.hashed(self.last_analysis) if self.last_analysis is not None else None)
        self._write('terminal.json',self.terminal)

    def _rows_intact(self,rows):
        for start,end,pin in self.committed:
            if math_core.hashed(dict(trials=rows[start:end]))!=pin:raise ValueError('committed root records changed')

    def _inspect(self,rows):
        self._rows_intact(rows)
        if len(encoded(dict(trials=rows)))>8*1024*1024:raise ValueError('sequential trial bytes exceeded')
        current=rows[self.offset:]
        if not current:return
        expected=[r for r in self.schedule if r['root']==self.binding['root_order'][self.index]]
        if [r.get('trial_id') for r in current]!=[r['trial_id'] for r in expected[:len(current)]]:
            raise ValueError('sequential trial prefix changed')
        observed=self.checker(current)
        critical=any(r.critical_regression for r in observed)
        if not critical and len(current)!=len(expected):return
        analysis=math_core.analyze(self.spec,self.owned_records+observed,self.binding,cohort_json=self.cohort_json)
        self._guard()
        inspection=dict(schema_version=1,index=self.index,root_id=expected[0]['root'],previous_sha256=self.previous,
            records_sha256=math_core.hashed(dict(trials=current)),analysis_sha256=math_core.hashed(analysis),
            complete=not critical or len(current)==len(expected))
        self._write(f'{self.index:04d}-inspected.json',inspection);self.previous=math_core.hashed(inspection)
        self.committed.append((self.offset,len(rows),inspection['records_sha256']))
        self.owned_records+=observed;self.last_analysis=analysis
        if inspection['complete']:self.index+=1
        self.offset=len(rows);self.active=False
        if analysis['status'] in ('supported','rejected','blocked','incomplete') or analysis['reason']=='empty_running_intersection':
            self.stop(analysis['status'],analysis['reason'],rows)

    def before(self,scheduled,rows):
        self.validate(self.spec)
        if self.active:self._inspect(rows)
        if self.terminal is not None:return False
        if self.index>=self.spec.sequential['max_roots']:
            self.stop('inconclusive','maximum_roots',rows);return False
        if not self.active:
            self._guard()
            if time.monotonic()+self.spec.sequential['root_seconds']>self.deadline:
                self.stop('inconclusive','root_budget_not_reserved',rows);return False
            root=self.binding['root_order'][self.index]
            if scheduled['root']!=root:raise ValueError('controller root order changed')
            self.root_deadline=time.monotonic()+self.spec.sequential['root_seconds']
            self._write(f'{self.index:04d}-started.json',dict(schema_version=1,index=self.index,root_id=root,
                previous_sha256=self.previous,sampling_sha256=math_core.hashed(self.binding),stage='root_reserved_before_dispatch'))
            self.active=True
        if [r['trial_id'] for r in self.schedule][len(rows)]!=scheduled['trial_id']:
            raise ValueError('controller scheduled trial changed')
        if time.monotonic()+2*self.spec.sequential['arm_seconds']>=self.root_deadline:
            self.stop('incomplete','partial_root_budget',rows);return False
        return True

    def finish(self,rows):
        if self.terminal is None:
            try:
                if self.active:self._inspect(rows)
                if self.terminal is None:self.stop('incomplete' if self.active else 'inconclusive',
                    'partial_root' if self.active else 'maximum_roots',rows)
            except (ValueError,OSError,TimeoutError):self.stop('incomplete','root_evidence_or_authority_incomplete',rows)
        status,reason=self.terminal['status'],self.terminal['reason']
        if status=='supported':
            try:self._rows_intact(rows);self._guard()
            except (ValueError,OSError,TimeoutError):status,reason='blocked','final_sequential_authority_unavailable'
        return dict(self.last_analysis or {},profile=math_core.PROFILE,status=status,reason=reason,
            completed_roots=self.index,sampling_sha256=math_core.hashed(self.binding),
            assumptions_status='declared_unverified',estimand=math_core.ESTIMAND,
            automatic_promotion=False,publication_allowed=False,cold_resume='unsupported',
            sampling_authority='owned_controller_inspections',observed_trials=len(rows),
            inference_inputs='committed_complete_roots_only_except_observed_critical_veto')


def replay(spec,path,rows,checker,*,cohort_json=None):
    """Recompute sealed controller custody; never starts or resumes an execution."""
    path=Path(path);binding=record(path/'binding.json',1024*1024)[0];math_core.validate_binding(spec,binding)
    terminal=record(path/'terminal.json')[0]
    expected_names={'binding.json','terminal.json'};previous=math_core.hashed(binding);offset=0;owned=[];analysis=None
    schedule=math_core.schedule(spec,binding)
    completed=terminal.get('completed_roots')
    if type(completed) is not int or not 0<=completed<=spec.sequential['max_roots']:raise ValueError('invalid root ledger count')
    index=0
    while (path/f'{index:04d}-inspected.json').exists():
        if index>=spec.sequential['max_roots']:raise ValueError('root inspection ceiling exceeded')
        started=record(path/f'{index:04d}-started.json')[0];inspected=record(path/f'{index:04d}-inspected.json')[0]
        root=binding['root_order'][index];expected=[r for r in schedule if r['root']==root]
        count=len(expected) if inspected.get('complete') is True else len(rows)-offset
        current=rows[offset:offset+count]
        if (started!=dict(schema_version=1,index=index,root_id=root,previous_sha256=previous,sampling_sha256=math_core.hashed(binding),stage='root_reserved_before_dispatch')
                or [r.get('trial_id') for r in current]!=[r['trial_id'] for r in expected[:count]]):
            raise ValueError('controller sampling/attempt custody changed')
        observed=checker(current);owned+=observed
        analysis=math_core.analyze(spec,owned,binding,cohort_json=cohort_json)
        actual=dict(schema_version=1,index=index,root_id=root,previous_sha256=previous,
            records_sha256=math_core.hashed(dict(trials=current)),analysis_sha256=math_core.hashed(analysis),
            complete=count==len(expected))
        if inspected!=actual or not actual['complete'] and analysis.get('reason')!='critical_regression':
            raise ValueError('sequential inspected boundary changed')
        previous=math_core.hashed(inspected);offset+=count
        expected_names.update((f'{index:04d}-started.json',f'{index:04d}-inspected.json'));index+=1
        if analysis['status'] in ('supported','rejected','blocked','incomplete') or analysis['reason']=='empty_running_intersection':break
    actual_completed=sum(record(path/f'{i:04d}-inspected.json')[0]['complete'] for i in range(index))
    if completed!=actual_completed or offset!=len(rows):raise ValueError('incomplete or foreign sequential prefix')
    if terminal.get('reason')=='maximum_roots' and completed!=spec.sequential['max_roots']:
        raise ValueError('sequential maximum root count changed')
    if terminal.get('status')=='inconclusive' and analysis is not None and analysis['status']!='inconclusive':
        raise ValueError('sequential stopping disposition was suppressed')
    if terminal.get('status')=='inconclusive' and terminal.get('reason') not in ('maximum_roots','root_budget_not_reserved'):
        raise ValueError('unsealed sequential failure')
    if terminal.get('status')!='inconclusive' and (analysis is None or terminal.get('status')!=analysis['status'] or terminal.get('reason')!=analysis['reason']):
        raise ValueError('forged sequential stop')
    if terminal!=dict(schema_version=1,status=terminal['status'],reason=terminal['reason'],completed_roots=completed,
            trials_sha256=math_core.hashed(dict(trials=rows)),previous_sha256=previous,
            analysis_sha256=math_core.hashed(analysis) if analysis is not None else None):raise ValueError('sequential terminal identity changed')
    if {p.name for p in path.iterdir()}!=expected_names:raise ValueError('interrupted or extra sequential custody files')
    return dict(analysis or {},profile=math_core.PROFILE,status=terminal['status'],reason=terminal['reason'],
        completed_roots=completed,sampling_sha256=math_core.hashed(binding),assumptions_status='declared_unverified',
        estimand=math_core.ESTIMAND,automatic_promotion=False,publication_allowed=False,cold_resume='unsupported',
            sampling_authority='owned_controller_inspections',observed_trials=len(rows),
            inference_inputs='committed_complete_roots_only_except_observed_critical_veto')
