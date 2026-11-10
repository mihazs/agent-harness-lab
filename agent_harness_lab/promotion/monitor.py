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
    if type(now) not in (int,float) or not math.isfinite(now): raise ValueError('invalid monitor clock')
    import json
    seen, tasks, outcomes, unknown, critical={},{},[],0,False
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
        started=event.get('task_started')
        if type(started) not in (int,float) or not math.isfinite(started) or started>now:
            unknown+=1; continue
        if version!=deployment or not policy.start<=started<policy.end: continue
        if type(event.get('outcome')) is not bool or event.get('provenance')!='executable_grader':
            unknown+=1; continue
        if event['task_id'] in tasks:
            if tasks[event['task_id']] != event['outcome']:
                return dict(state='conflict',reason='contradictory_task_outcomes',automatic_rollback=False)
            unknown+=1; continue
        tasks[event['task_id']]=event['outcome']
        outcomes.append(event['outcome'])
        critical|=event.get('critical') is True and event['outcome'] is False
    state='paused' if critical else 'observing' if now<policy.end or len(outcomes)<policy.min_exposure else 'stable'
    return dict(state=state,verified_exposure=len(outcomes),successes=sum(outcomes),unknown=unknown,
                policy_version=policy.version,causal_attribution='unestablished',
                action='manual_review' if critical else 'observe',automatic_rollback=False)


def triage_audit_observation(policy,receipt,frame,store_path,binding,rubric,*,plan=None,trusted_heads=None,include_composition=False):
    """Recompute a private triage-audit observation; identifiers are declarations, not execution proof."""
    from agent_harness_lab.diagnostics import audit_corpus, audit_fixed, audit_sampling, audit_stratified, triage_policy
    triage_policy.check_policy(policy)
    policy_sha=triage_policy.policy_sha256(policy);model_sha=policy['model_sha256']
    audit_corpus.rubric(rubric)
    classes=policy['classes']; labels=set(rubric['markers'].values())
    if (audit_sampling.NO_INCIDENT not in classes or not labels<=set(classes)
            or classes[audit_sampling.NO_INCIDENT]['severity']!='none'
            or any(classes[name]['severity']=='none' for name in labels if name!=audit_sampling.NO_INCIDENT)):
        raise ValueError('audit rubric classes differ from triage policy semantics')
    if not isinstance(receipt,dict):raise ValueError('audit receipt required')
    if receipt.get('profile')==audit_sampling.RECEIPT_PROFILE_V2 and plan is None:
        declared=receipt
    elif receipt.get('profile') in (audit_fixed.RECEIPT_PROFILE,audit_stratified.RECEIPT_PROFILE) and isinstance(plan,dict):
        declared=plan
    else:raise ValueError('supported audit receipt and plan required')
    if model_sha is None or declared.get('policy_sha256')!=policy_sha or declared.get('model_sha256')!=model_sha:
        raise ValueError('audit policy or model declaration differs')
    report=audit_corpus.estimate(receipt,frame,store_path,binding,rubric,trusted_heads,plan=plan,
                                 include_composition=include_composition)
    return dict(schema_version=1,profile=('triage-audit-monitor-composition-v1' if include_composition
                                          else 'triage-audit-monitor-observation-v1'),policy_sha256=policy_sha,
        model_sha256=model_sha,audit_receipt_sha256=receipt['receipt_sha256'],audit_report=report,
        identity_binding='caller_declared_identifiers_only',source_execution='unverified',
        policy_application='not_observed',alert_decisions='caller_declared_unverified',
        reviewer_identity='unverified',assignment_timing='unauthenticated',frame_origin='unauthenticated',
        action='observe_only',causal_attribution='unestablished',automatic_policy_update=False,
        automatic_rollback=False,promotion_authority='none',publication_allowed=False)
