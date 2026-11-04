"""Fail-closed behavioral promotion. Implemented statistics are not certification."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PromotionDecision:
    status: str
    reason: str
    automatic: bool = False
    isolation: str = 'unknown'
    declared_prerequisites: dict = field(default_factory=dict)


def isolation_qualification(scope=None):
    """Derive the isolation element only from live owned producer facts, never from a caller."""
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
    return len(observed) == len(trials) == len(expected) and set(observed) == expected


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
            receipt_bytes=len(scope._state()),receipt_encoding=RECEIPT_ENCODING,
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
