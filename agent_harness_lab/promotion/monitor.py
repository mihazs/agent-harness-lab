"""Fixed-window, observe-only delayed outcomes and drift signals. Drift is not causal proof."""
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
                return dict(state='conflict',reason='conflicting_duplicate_event',stability_claim=False,automatic_rollback=False)
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
                return dict(state='conflict',reason='contradictory_task_outcomes',stability_claim=False,automatic_rollback=False)
            unknown+=1; continue
        tasks[event['task_id']]=event['outcome']
        outcomes.append(event['outcome'])
        critical|=event.get('critical') is True and event['outcome'] is False
    # A closed window is only observation-complete: `no_alert` never claims stability (earlier releases named it `stable`).
    state=('paused' if critical else 'observing' if now<policy.end
           else 'insufficient_evidence' if len(outcomes)<policy.min_exposure else 'no_alert')
    return dict(state=state,verified_exposure=len(outcomes),successes=sum(outcomes),unknown=unknown,
                policy_version=policy.version,causal_attribution='unestablished',stability_claim=False,
                action='manual_review' if critical else 'observe',automatic_rollback=False)


SIGNAL_PROFILE='promotion-monitor-signal-v1'
SIGNAL_REQUESTS=dict(capture='investigate_capture',task_mix='review_task_mix',performance='reproduce_performance')


def signal(deployment,plan,reference,receipt):
    """Fold one sealed drift receipt into a versioned signal that asks for a look and never authorizes a rollback.

    A drift receipt does not say which deployment a run used, so it cannot be attributed to this one."""
    from agent_harness_lab.diagnostics import drift
    if not isinstance(deployment,str) or not deployment: raise ValueError('signal requires a deployment id')
    plan_sha=drift.check_receipt(plan,reference,receipt)
    try:
        window=dict(receipt['window']);index=window['index']
        rows={name:dict(state=receipt['channels'][name]['state'],reasons=list(receipt['channels'][name]['reasons']))
              for name in SIGNAL_REQUESTS}
        capture={key:receipt['channels']['capture'][key] for key in ('runs','eligible_runs','coverage','reference_coverage')}
        uncertainty={name:{key:receipt['channels'][name][key] for key in ('tested','per_window_alpha')}
                     for name in ('task_mix','performance')}
    except (KeyError,TypeError,AttributeError): raise ValueError('drift receipt lacks the channels a signal needs') from None
    if (type(index) is not int or not 0<=index<plan['max_windows'] or window.get('max_windows')!=plan['max_windows']
            or any(row['state'] not in ('alert','no_alert','insufficient_evidence') for row in rows.values())):
        raise ValueError('drift receipt does not fit the frozen schedule or states')
    requests=[SIGNAL_REQUESTS[name] for name,row in rows.items() if row['state']=='alert']
    return dict(schema_version=1,profile=SIGNAL_PROFILE,deployment=deployment,
        detector=dict(profile=receipt['profile'],plan_sha256=plan_sha,reference_sha256=reference['sha256'],receipt_sha256=receipt['sha256']),
        window=window,channels=rows,
        capture=capture,
        uncertainty=uncertainty,
        multiplicity=dict(method=plan['multiplicity'],window=index,max_windows=plan['max_windows'],
                          windows_remaining=plan['max_windows']-index-1,reset=plan['reset_policy']),
        requests=requests,action='review' if requests else 'observe',deployment_state='unchanged',
        regression_label='not_established',causal_attribution='unestablished',deployment_attribution='not_established',
        stability_claim=False,receipt_authority='unverified',automatic_rollback=False,rollback_authorization=False,
        promotion_authority='none',publication_allowed=False)


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


def triage_audit_gate(policy,receipt,frame,store_path,binding,rubric,requirement,*,decision_report=None,plan=None,trusted_heads=None):
    """Judge a recomputed private audit against a preregistered requirement; the verdict carries no authority."""
    from agent_harness_lab.diagnostics import audit_gate, audit_summary
    observation=triage_audit_observation(policy,receipt,frame,store_path,binding,rubric,plan=plan,trusted_heads=trusted_heads)
    return audit_gate.evaluate(audit_summary.summarize(observation,decision_report),policy,requirement)
