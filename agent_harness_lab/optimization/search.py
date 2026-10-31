"""Default-off controller search with durable charges and untouched final callback."""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from agent_harness_lab.evaluation.integrity import relative
from agent_harness_lab.monitoring.state import atomic_json
from .pareto import frontier


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: str
    parents: tuple
    mechanism: str
    changes: dict
    evidence_refs: tuple
    hypothesis: str
    falsifier: str

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.changes,sort_keys=True).encode()).hexdigest()

    def validate(self, allowed_files):
        if not self.candidate_id or not self.mechanism or not self.evidence_refs or not self.hypothesis or not self.falsifier:
            raise ValueError('candidate requires causal hypothesis/evidence/falsifier')
        if len(self.changes)>100 or not set(self.changes)<=set(allowed_files):
            raise ValueError('candidate exceeds authorized surface')
        size=0
        for path, content in self.changes.items():
            relative(path)
            if content is not None and not isinstance(content,str):
                raise ValueError('patch content must be text or deletion')
            size+=len((content or '').encode())
        if size>1024*1024: raise ValueError('candidate patch byte bound')


def compose(left,right,candidate_id):
    overlap=left.changes.keys() & right.changes.keys()
    if any(left.changes[k]!=right.changes[k] for k in overlap):
        raise ValueError('composition conflict')
    return CandidateRecord(candidate_id,(left.candidate_id,right.candidate_id),
        left.mechanism+'+'+right.mechanism,{**left.changes,**right.changes},
        left.evidence_refs+right.evidence_refs,'Combined patch needs interaction evaluation',
        'Any component or interaction regression rejects the composition')


def search(path, baseline, propose, evaluate, certify, budget, allowed_files, *, enabled=False, variants=3):
    if not enabled: return dict(status='disabled',automatic_promotion=False)
    if not 1<=variants<=3: raise ValueError('invalid bounded candidate pool')
    path=Path(path)
    if path.exists():
        state=json.loads(path.read_text())
        if state.get('schema_version')!=1: raise ValueError('unsupported search state')
        # Interrupted charged stages are never silently rerun.
        return dict(state,status=state.get('status','interrupted'),resumed_without_execution=True)
    state=dict(schema_version=1,status='searching',candidates=[],rejections=[],budget_events=[],automatic_promotion=False)
    seen=set(); candidate_ids=set()
    run_id=hashlib.sha256(str(path.absolute()).encode()).hexdigest()[:16]
    def save():
        state['budget_events']=budget.events
        atomic_json(path,state)
    for index in range(variants+1):
        kind='baseline' if index==0 else 'proposal'
        event_id=f'{run_id}:{kind}:{index}'
        if not budget.consume(kind,1,event_id):
            state['status']='budget_exhausted'; save(); return state
        state.update(stage='in_progress',event_id=event_id); save()
        candidate=baseline if index==0 else propose(index)  # Development-only callback; no final data.
        try:
            candidate.validate(allowed_files)
        except (ValueError,TypeError,AttributeError):
            state['rejections'].append(dict(index=index,reason='invalid_or_unauthorized_proposal')); save(); continue
        if candidate.digest in seen or candidate.candidate_id in candidate_ids:
            state['rejections'].append(dict(candidate_id=candidate.candidate_id,reason='duplicate_patch')); save(); continue
        seen.add(candidate.digest); candidate_ids.add(candidate.candidate_id)
        if not budget.consume('development_trial',1,run_id+':trial:'+str(index)):
            state['status']='budget_exhausted'; save(); return state
        save()
        result=evaluate(candidate)
        state['candidates'].append(dict(candidate_id=candidate.candidate_id,digest=candidate.digest,
            parents=list(candidate.parents),record=asdict(candidate),safe=result.get('safe') is True,
            metrics=result.get('metrics',{}),evidence_scope='development_only'))
        state['stage']='development_complete'; save()
    choices=frontier(state['candidates'])
    # Ambiguous frontiers/ties keep the no-op; point estimates are not certification.
    best=next((c for c in choices if c['candidate_id']==baseline.candidate_id),None)
    if best is None and len(choices)==1: best=choices[0]
    if best is None or best['candidate_id']==baseline.candidate_id:
        state['status']='no_change'; save(); return state
    if not budget.consume('final_certification',1,run_id+':final:'+best['candidate_id'],final=True):
        state['status']='budget_exhausted'; save(); return state
    state.update(status='certification_in_progress',finalist=best['candidate_id']); save()
    state['certification']=certify(CandidateRecord(**{**best['record'],'parents':tuple(best['record']['parents']),
                                                   'evidence_refs':tuple(best['record']['evidence_refs'])}))
    state['status']='recommended' if state['certification'].get('status')=='recommended' else 'rejected_or_inconclusive'
    state['stage']='complete'; save(); return state
