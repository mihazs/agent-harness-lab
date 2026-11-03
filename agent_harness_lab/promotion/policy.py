"""Fail-closed behavioral promotion. Implemented statistics are not certification."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PromotionDecision:
    status: str
    reason: str
    automatic: bool = False


def decide(spec, analysis, *, isolation_verified=False, effective_runtime_verified=False,
           graders_audited=False, lineage_reviewed=False, certification_reserved=False):
    spec.validate()
    if analysis['status'] in ('rejected', 'blocked', 'incomplete'):
        return PromotionDecision(analysis['status'], analysis.get('reason', 'invalid_evidence'))
    if spec.sequential is not None:return PromotionDecision('blocked','conditional_sequential_cannot_certify')
    if spec.cohort_manifest_sha256 is not None:
        return PromotionDecision('blocked','preservation_promotion_integration_unavailable')
    if spec.profile != 'certification':
        return PromotionDecision('inconclusive', 'descriptive_smoke_cannot_certify')
    if analysis.get('oracle_validity') != 'current':
        return PromotionDecision('blocked', 'oracle_history_unverified_or_invalidated')
    if not all((isolation_verified, effective_runtime_verified, graders_audited, lineage_reviewed, certification_reserved)):
        return PromotionDecision('blocked', 'certification_prerequisites_unverified')
    if analysis['status'] != 'supported':
        return PromotionDecision('inconclusive', analysis.get('reason', 'uncertain'))
    # Recommendation only until a controller integrates authenticated attestations.
    return PromotionDecision('recommended', 'fixed_final_evidence_supports_declared_claim')



def legacy_coverage(manifest, trials):
    expected = {(version, task['id']) for version in ('baseline', 'candidate') for task in manifest['tasks']}
    observed = [(trial.get('version'), trial.get('task')) for trial in trials if isinstance(trial, dict)]
    return len(observed) == len(trials) == len(expected) and set(observed) == expected


def implementation_prerequisites(scope=None, *, declarations=None):
    """Read live producer facts; legacy recommendation flags stay declarations."""
    from .prerequisites import _Scope,PROFILE,MAX_DISPATCHES,MAX_BYTES
    from agent_harness_lab.private_journal import encoded
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
            receipt_bytes=None,receipt_bytes_limit=MAX_BYTES,roles=[],capability_probes=None,
            independence='unverified',task_fixture_coverage='unverified'))
    if type(scope) is not _Scope:return packet
    try:data=_Scope.read(scope)
    except (ValueError,OSError,TypeError,KeyError,AttributeError,RecursionError):return packet
    events=data['events'];complete=bool(events) and all(row['complete'] for row in events)
    packet.update(status='observed' if complete else 'unknown',
        coverage=dict(dimension='post_preflight_dispatch_admissions',admitted=len(events),
            completed=sum(row['complete'] for row in events),limit=MAX_DISPATCHES,
            receipt_bytes=len(encoded(data)),
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
        audits=[row['permission'] for row in events if row['stage']=='role']
        packet['capability_probe_permission_results']=[row['permission']['qualification'] if row['permission'] else 'unrequested'
            for row in events if row['stage']=='capability_probe']
        packet['permission_result_capture']=('violated' if any(row and row['qualification']=='violated' for row in audits)
            else 'qualified' if audits and all(row and row['qualification']=='qualified' for row in audits)
            else 'unrequested' if all(row is None for row in audits) else 'unknown')
    return packet
