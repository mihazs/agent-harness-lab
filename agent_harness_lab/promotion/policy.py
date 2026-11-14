"""Fail-closed behavioral promotion. Implemented statistics are not certification."""
from dataclasses import dataclass, field

from agent_harness_lab.evaluation.vacuity import receipt as vacuity_receipt


@dataclass(frozen=True)
class PromotionDecision:
    status: str
    reason: str
    automatic: bool = False
    isolation: str = 'unknown'
    declared_prerequisites: dict = field(default_factory=dict)


def isolation_qualification(scope=None):
    """Derive the isolation element from live owned producer facts, never from a caller-declared value.

    In-process code that holds the live scope object is trusted; this is not a sandbox boundary.
    """
    packet=implementation_prerequisites(scope);checks=packet['checks']
    qualified=(packet['status']=='observed' and packet['restricted_process_protocol']['status']=='qualified'
        and packet['permission_result_capture']=='qualified' and all(row['status']=='verified' for row in checks.values()))
    return 'qualified' if qualified else 'unknown'


def decide(spec, analysis, *, prerequisite_scope=None, isolation_verified=None, effective_runtime_verified=False,
           graders_audited=False, lineage_reviewed=False, certification_reserved=False):
    """`isolation_verified` is a legacy declaration: it is recorded and never grants isolation."""
    spec.validate()
    isolation=isolation_qualification(prerequisite_scope)
    declared={} if isolation_verified is None else {'isolation_verified':dict(status='declared_unverified',
        value=isolation_verified if type(isolation_verified) is bool else 'non_boolean')}
    def result(status,reason):return PromotionDecision(status,reason,isolation=isolation,declared_prerequisites=declared)
    if analysis['status'] in ('rejected', 'blocked', 'incomplete'):
        return result(analysis['status'], analysis.get('reason', 'invalid_evidence'))
    if spec.sequential is not None:return result('blocked','conditional_sequential_cannot_certify')
    if spec.cohort_manifest_sha256 is not None:
        return result('blocked','preservation_promotion_integration_unavailable')
    if spec.profile != 'certification':
        return result('inconclusive', 'descriptive_smoke_cannot_certify')
    if analysis.get('oracle_validity') != 'current':
        return result('blocked', 'oracle_history_unverified_or_invalidated')
    if not all((isolation=='qualified', effective_runtime_verified, graders_audited, lineage_reviewed, certification_reserved)):
        return result('blocked', 'certification_prerequisites_unverified')
    if analysis['status'] != 'supported':
        return result('inconclusive', analysis.get('reason', 'uncertain'))
    # Recommendation only until a controller integrates authenticated attestations.
    return result('recommended', 'fixed_final_evidence_supports_declared_claim')



def legacy_coverage(manifest, trials):
    expected = {(version, task['id']) for version in ('baseline', 'candidate') for task in manifest['tasks']}
    observed = [(trial.get('version'), trial.get('task')) for trial in trials if isinstance(trial, dict)]
    # No declared case and no trial is no_eligible_cases and zero_trials, never complete coverage (#805).
    return bool(expected) and len(observed) == len(trials) == len(expected) and set(observed) == expected


SINGLE_RUN_PROFILE = 'ahl-single-run-decision-disposition-v1'
PAIR_KINDS = ('candidate_only_passed', 'baseline_only_passed', 'both_passed', 'both_failed', 'unknown')


def single_run_decision(manifest, trials, *, inputs_unchanged, prerequisites, prerequisite_scope=None, bindings=None, vacuity=()):
    """Explain why one legacy paired run cannot support a recommendation.

    Counts are observed patterns only. The disposition never grants recommendation,
    application, promotion or publication authority; outcomes other than an explicit
    pass/fail remain unknown rather than becoming either label.
    """
    tasks = manifest['tasks']
    if len({task['id'] for task in tasks}) != len(tasks) or not legacy_coverage(manifest, trials):
        raise ValueError('single-run decision requires exact unique legacy trial coverage')
    declared = {task['id']: task.get('split') for task in tasks}
    # Splits come from the manifest; a trial reporting another split is unknown evidence.
    mismatched = sum(trial.get('split') != declared[trial['task']] for trial in trials)
    outcome = {(trial['version'], trial['task']): trial.get('status')
               if trial.get('status') in ('passed', 'failed') and trial.get('split') == declared[trial['task']] else None
               for trial in trials}
    by_split = {}
    for task in tasks:
        base, cand = outcome['baseline', task['id']], outcome['candidate', task['id']]
        kind = ('unknown' if None in (base, cand) else 'both_passed' if base == cand == 'passed'
                else 'both_failed' if base == cand else 'candidate_only_passed' if cand == 'passed' else 'baseline_only_passed')
        split = task.get('split') if isinstance(task.get('split'), str) else 'undeclared'
        by_split.setdefault(split, dict.fromkeys(PAIR_KINDS, 0))[kind] += 1
    pairs = {kind: sum(row[kind] for row in by_split.values()) for kind in PAIR_KINDS}
    roots = [task.get('root') for task in tasks]
    declared_roots = len(set(roots)) if roots and all(isinstance(root, str) and root for root in roots) else None
    def failed(version, split=None):
        return any(outcome[version, task['id']] == 'failed' and (split is None or task.get('split') == split) for task in tasks)
    if vacuity: pattern = 'vacuous_evaluation'
    elif not inputs_unchanged: pattern = 'frozen_inputs_changed'
    elif pairs['unknown']: pattern = 'unknown_trial_outcomes'
    elif failed('candidate'): pattern = 'candidate_failures_observed'
    elif failed('baseline', 'regression'): pattern = 'baseline_control_failures_observed'
    elif failed('baseline', 'development'): pattern = 'candidate_passed_where_baseline_failed'
    elif pairs['candidate_only_passed']: pattern = 'difference_outside_gate_splits'
    else: pattern = 'no_observed_difference'
    isolation = isolation_qualification(prerequisite_scope)
    unmet = {name: value.get('reason') for name, value in prerequisites['mandatory_certification'].items()
             if value.get('status') != 'verified'}
    reasons = ['single_run_per_task_arm', 'no_frozen_experiment_plan', 'descriptive_smoke_cannot_certify',
               'independent_roots_undeclared' if declared_roots is None else 'independent_roots_unverified']
    if vacuity: reasons.append('vacuous_evaluation')
    if pairs['unknown']: reasons.append('unknown_trial_outcomes')
    if mismatched: reasons.append('trial_split_mismatch')
    if not inputs_unchanged: reasons.append('frozen_inputs_changed')
    if isolation != 'qualified': reasons.append('isolation_qualification_unknown')
    if unmet: reasons.append('certification_prerequisites_unverified')
    promotion = (PromotionDecision('blocked', 'frozen_inputs_changed', isolation=isolation) if not inputs_unchanged
                 else PromotionDecision('inconclusive', 'descriptive_smoke_cannot_certify', isolation=isolation))
    return dict(schema_version=1, profile=SINGLE_RUN_PROFILE, evidence_scope='single_run_descriptive_only',
        disposition='insufficient_evidence' if inputs_unchanged and not vacuity else 'invalid_evidence',
        next_evidence='requires_repeated_paired_experiment', observed_pattern=pattern, reasons=reasons,
        unmet_prerequisites=unmet,
        observed=dict(tasks=len(tasks), trials=len(trials), repeats_per_task_arm=1, split_mismatched_trials=mismatched,
                      declared_roots=declared_roots, pairs=pairs, by_split=by_split),
        promotion=dict(status=promotion.status, reason=promotion.reason, automatic=promotion.automatic,
                       isolation=promotion.isolation),
        bindings=dict(bindings or {}), independence='unverified', native_identity='unverified',
        recommendation_authority=False, application_eligible=False, publication_allowed=False,
        automatic_promotion=False, adoption_authority=False, **(dict(vacuity=vacuity_receipt(vacuity)) if vacuity else {}))


def implementation_prerequisites(scope=None, *, declarations=None):
    """Read live producer facts; legacy recommendation flags stay declarations."""
    from .prerequisites import _Scope,PROFILE,MAX_DISPATCHES,MAX_BYTES,RECEIPT_ENCODING
    from agent_harness_lab.evaluation.runtime_boundary import PROFILE as BOUNDARY_PROFILE
    packet=dict(schema_version=1,profile=PROFILE,status='unrequested' if scope is None else 'unknown',
        verification_scope='post_preflight_owned_dispatches_only',publication_allowed=False,automatic_promotion=False,
        adoption_authority=False,native_identity='unverified',scientific_eligibility='unverified',
        restricted_process_protocol=dict(profile=BOUNDARY_PROFILE,status='unknown',scope='post_preflight_owned_dispatches_only'),
        mandatory_certification={name:dict(status='unverified',reason=reason) for name,reason in (
            ('role_isolation','whole_boundary_qualification_unavailable'),
            ('audited_task_lineage','independent_root_authority_unavailable'),
            ('repeated_final_evaluation','current_certification_decision_required'),
            ('effective_runtime_verification','effective_instruction_configuration_identity_unavailable'))},
        declared_prerequisites={name:dict(status='declared_unverified',value=value)
            for name,value in (declarations or {}).items()},
        checks={name:dict(status='unknown') for name in (
            'owned_dispatch_completion','checked_worker_rootfs','fresh_grader_artifact_handoff')},
        permission_result_capture='unrequested' if scope is None else 'unknown',
        permission_result_scope='project_role_errno_results_only',
        coverage=dict(dimension='post_preflight_dispatch_admissions',admitted=None,completed=None,limit=MAX_DISPATCHES,
            receipt_bytes=None,receipt_encoding=RECEIPT_ENCODING,receipt_bytes_limit=MAX_BYTES,roles=[],capability_probes=None,
            independence='unverified',task_fixture_coverage='unverified'))
    if type(scope) is not _Scope:return packet
    try:data=_Scope.read(scope)
    except (ValueError,OSError,TypeError,KeyError,AttributeError,RecursionError):return packet
    events=data['events'];complete=bool(events) and all(row['complete'] for row in events)
    packet.update(status='observed' if complete else 'unknown',
        coverage=dict(dimension='post_preflight_dispatch_admissions',admitted=len(events),
            completed=sum(row['complete'] for row in events),limit=MAX_DISPATCHES,
            receipt_bytes=len(_Scope._state(scope)),receipt_encoding=RECEIPT_ENCODING,
            receipt_bytes_limit=MAX_BYTES,roles=sorted({row['role'] for row in events if row['stage']=='role'}),
            capability_probes=sum(row['stage']=='capability_probe' for row in events),
            independence='unverified',task_fixture_coverage='unverified'),
        bindings=[{key:binding[key] for key in ('sha256','baseline_sha256','candidate_sha256','manifest_sha256')}
            for binding in data['bindings']])
    if complete:
        boundaries=[row.get('boundary',{}) for row in events]
        packet['restricted_process_protocol'].update(status='qualified' if all(row.get('status')=='qualified' for row in boundaries) else 'unknown',
            observations=len(boundaries),roles=sorted({row['role'] for row in events if row['stage']=='role'}),
            first_unqualified=next((dict(ordinal=index,role=events[index]['role'],reason=row.get('reason','capture_unavailable'))
                for index,row in enumerate(boundaries) if row.get('status')!='qualified'),None))
        packet['checks']['owned_dispatch_completion']=dict(status='verified',observations=len(events))
        if all(row['image_sha256']==scope.image for row in events):
            packet['checks']['checked_worker_rootfs']=dict(status='verified',sha256=scope.image)
        if data['pairs']:
            packet['checks']['fresh_grader_artifact_handoff']=dict(status='verified',observations=len(data['pairs']),
                scope='observed_selected_path_and_byte_pairs_only',oracle_validity='unverified',
                selected_paths_limit=100,selected_paths_observed=[pair['selected_paths'] for pair in data['pairs']])
        requests=data.get('instruction_requests')
        if requests:
            from agent_harness_lab.evaluation.instruction_context import PROFILE as INSTRUCTION_PROFILE
            executor_scopes={row['binding']['scope_id'] for row in requests if row['binding']['role']=='executor'}
            observed_scopes={row.get('instruction_scope') for row in events if row['role']=='executor' and row['stage']=='role'}
            packet['controlled_instruction_protocol']=dict(profile=INSTRUCTION_PROFILE,
                status='qualified' if executor_scopes and executor_scopes<=observed_scopes and data['pairs'] else 'unknown',
                requests=len(requests),completed=sum(row['complete'] for row in requests),
                scope='controller_injection_and_transmission_only',instruction_loading='executor_root_AGENTS_only',served_model='unverified',native_identity='unverified',
                semantic_adherence='unverified',publication_allowed=False)
        audits=[row['permission'] for row in events if row['stage']=='role']
        packet['capability_probe_permission_results']=[row['permission']['qualification'] if row['permission'] else 'unrequested'
            for row in events if row['stage']=='capability_probe']
        packet['permission_result_capture']=('violated' if any(row and row['qualification']=='violated' for row in audits)
            else 'qualified' if audits and all(row and row['qualification']=='qualified' for row in audits)
            else 'unrequested' if all(row is None for row in audits) else 'unknown')
    return packet
