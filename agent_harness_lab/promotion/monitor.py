"""Fixed-window, observe-only delayed outcomes. Drift is not causal proof."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class MonitorPolicy:
    version: str
    start: float
    end: float
    min_exposure: int

    def validate(self):
        if not self.version or not all(type(v) in (int,float) and math.isfinite(v) for v in (self.start,self.end)) or self.end<=self.start or type(self.min_exposure) is not int or self.min_exposure<=0:
            raise ValueError('invalid frozen monitoring window')


def monitor(deployment,task_versions,events,policy,now):
    policy.validate()
    import json
    seen, tasks, outcomes, unknown, critical={},set(),[],0,False
    for event in events:
        signature=json.dumps(event,sort_keys=True)
        if event['event_id'] in seen:
            if seen[event['event_id']]!=signature:
                return dict(state='conflict',reason='conflicting_duplicate_event',automatic_rollback=False)
            continue
        seen[event['event_id']]=signature
        version=task_versions.get(event.get('task_id'))
        if version is None or event.get('deployment')!=version:
            unknown+=1; continue
        if version!=deployment or not policy.start<=event['task_started']<policy.end: continue
        if type(event.get('outcome')) is not bool or event.get('provenance')!='executable_grader':
            unknown+=1; continue
        if event['task_id'] in tasks:
            unknown+=1; continue
        tasks.add(event['task_id'])
        outcomes.append(event['outcome'])
        critical|=event.get('critical') is True and event['outcome'] is False
    state='paused' if critical else 'observing' if now<policy.end or len(outcomes)<policy.min_exposure else 'stable'
    return dict(state=state,verified_exposure=len(outcomes),successes=sum(outcomes),unknown=unknown,
                policy_version=policy.version,causal_attribution='unestablished',
                action='manual_review' if critical else 'observe',automatic_rollback=False)
