"""Human-readable, descriptive exit guidance; it never grants execution authority."""

REMEDIATION = {
    'no_data': 'Run ahl audit . for a zero-model-cost static convention check; ahl improve may incur model costs.',
    'analysis_incomplete': 'Review caveats in analysis.json; restore missing coverage or remove explicit limits before interpreting absence of findings.',
    'analysis_observed': 'Review the captured evidence and assess a candidate with ahl improve when ready.',
    'select_one_runtime_with_agent': 'Several supported runtimes were found; rerun with --agent codex or --agent claude (or set runtime in ahl.json).',
    'agent_runtime_unavailable': 'No requested runtime executable was found; install it or pass its trusted path with --codex or --claude. Authentication is not checked by AHL.',
    'trial_budget_requires_two_preparation_and_six_paired_trials': 'Raise the trial limit to at least 8 in ahl.json limits.trials.',
    'configured_bank_invalid_or_unavailable': 'Select an accessible valid project bank or use the starter bank; review its configuration before retrying.',
    'configured_bank_digest_mismatch': 'The configured bank changed; review it and set bank.sha256 in ahl.json to its new digest.',
    'configured_profile_does_not_allow_cooperative_improvement': 'ahl.json profile is not cooperative; review it and set profile to cooperative and runtime to {name: codex or claude, executable: <absolute path>} to allow model execution.',
    'configured_bank_requires_cooperative_runtime': 'Pinned project banks run only on the codex or claude runtime; set runtime in ahl.json to {name: codex or claude, executable: <absolute path>}.',
    'configured_runtime_differs_from_requested_runtime': 'The requested runtime or executable differs from ahl.json; drop the override or update runtime in ahl.json.',
    'configured_bank_cannot_combine_with_managed_experiment': 'Run without --experiment-request/--evaluation-store, or use the starter bank.',
    'configured_managed_bank_requires_controlled_workflow': 'Managed banks run only through the controlled workflow; use a non-managed bank.',
    'configured_bank_requires_development_regression_and_heldout': 'Add development, calibration and held-out (certification or transfer) partitions to the bank.',
    'configured_bank_shared_trial_budget_exhausted': 'Raise limits.trials in ahl.json to at least 1 + 2 * (bank task count).',
    'configured_shared_trial_budget_infeasible': 'Increase the shared trial limit for the requested comparison.',
    'fixed_comparison_inference_budget_infeasible': 'Increase the declared inference budget for the comparison.',
    'precision_target_infeasible': 'Review the requested precision target and available budget.',
    'inference_sandbox_unavailable': 'Provide the required inference sandbox capabilities.',
    'grader_sandbox_unavailable': 'Provide the required grader sandbox capabilities.',
    'capability_preflight_unavailable': 'Restore the required preflight capability before retrying.',
    'offline_sandbox_unavailable': 'Provide the required offline sandbox capabilities.',
    'instruction_validation_blocked': 'Review the instruction validation findings before retrying.',
    'instruction_removal_inconclusive': 'Restore the missing behavioral controls before evaluating the removal.',
    'instruction_removal_rejected': 'Review the failed behavioral controls before revising the removal.',
    'shared_trial_budget_exhausted': 'Review and increase the shared trial budget if appropriate.',
    'proposer_no_change': 'Review the evidence and proposal before deciding whether another candidate is needed.',
    'candidate_identical_to_baseline': 'Review the unchanged candidate and its supporting evidence.',
    'evaluation_no_improvement': 'Review the evaluation results before proposing another candidate.',
    'permission_qualification_not_met': 'Review the permission qualification and restore the required isolation.',
    'missing_or_duplicate_trials': 'Review the trial records and restore complete, unique trial coverage.',
    'restricted_process_protocol_unqualified': 'Restore the required restricted-process qualification.',
    'unresolved_operation_requires_hook_contract': 'Supply the required hook contract before considering the operation.',
    'hook_adoption_requires_explicit_authorization': 'Review the hook change and its authorization requirements.',
    'configuration_requires_recommendation_only': 'Review the recommendation under the configured application policy.',
    'fresh_comparison_incomplete': 'Review the incomplete comparison and its missing trial coverage.',
    'certification_prerequisites_unverified': 'Verify the certification prerequisites before drawing a conclusion.',
    'configured_responses_requires_controlled_improvement': 'Select the controlled Responses improvement workflow.',
    'configured_isolated_runtime_unsupported': 'Select a supported runtime profile.',
    'offline_configuration_forbids_model_execution': 'Use an offline operation or explicitly configure a supported runtime.',
    'configured_executable_differs_from_requested_executable': 'Align the executable with the project configuration.',
    'configured_executable_unavailable': 'Provide the configured executable at its trusted path.',
    'configuration_changed_before_execution': 'Review the changed configuration before retrying.',
    'preflight_budget_exhausted': 'Review the preflight budget before retrying.',
    'codex_credentials_unavailable': 'Authenticate the selected runtime before requesting model execution.',
    'frozen_inputs_changed': 'Review the changed frozen inputs before starting a fresh evaluation.',
    'inference_aborted_after_failure': 'Review the recorded inference failure before retrying.',
    'isolation_qualification_unverified': 'Verify the required isolation qualification before retrying.',
    'inference_transport_failed_no_retry': 'Inspect the recorded inference transport failure before any retry.',
    'prior_inference_request_unresolved_no_retry': 'Resolve the earlier inference request before issuing another.',
    'inference_request_count_exhausted': 'Review the configured inference request limit.',
    'inference_request_bytes_exhausted': 'Review the configured inference request byte limit.',
    'inference_response_bytes_exhausted': 'Review the configured inference response byte limit.',
    'inference_protocol_error_no_retry': 'Inspect the recorded inference protocol error before any retry.',
    'inference_tool_count_exhausted': 'Review the configured inference tool-call limit.',
    'executor_containment_or_state_failure': 'Inspect the executor containment and state failure before retrying.',
}

STATUS_ACTION = {
    'no_change': 'Review the evidence and proposal before deciding whether another candidate is needed.',
    'applied': 'Review the recorded deployment and its rollback information.',
    'rejected': 'Review the recorded evaluation and rejection evidence.',
    'blocked': 'Review the reported prerequisite before retrying.',
    'inconclusive': 'Review the incomplete evidence in the private receipt.',
    'budget_exhausted': 'Review the recorded budget before retrying.',
    'interrupted': 'Review the interrupted run before starting another.',
    'invalid_input': 'Review and correct the reported input error before retrying.',
    'invalid_proposal': 'Review the invalid proposal and its recorded validation.',
    'execution_error': 'Review the recorded execution error before retrying.',
    'evaluation_failed': 'Review the failed evaluation records before retrying.',
    'incomplete': 'Review the incomplete run and missing evidence.',
    'requires_intervention': 'Review the recorded intervention requirement before retrying.',
    'conflict': 'Review the recorded conflict before retrying.',
    'legacy_attempted': 'Review the recorded legacy operation before retrying.',
    'setup_error': 'Review and correct the recorded setup error.',
}


def summary(result):
    status = result.get('status')
    analysis = result.get('analysis') or {}
    evidence = analysis.get('evidence_state')
    if status == 'analyzed':
        reason = {'empty': 'no_data', 'incomplete': 'analysis_incomplete', 'observed': 'analysis_observed'}.get(evidence)
        category = {'empty': 'No data', 'incomplete': 'Inconclusive', 'observed': 'Evidence observed'}.get(evidence, 'Inconclusive')
    else:
        reason = result.get('reason')
        category = {'no_change': ('No change selected' if reason in ('evaluation_no_improvement', 'candidate_identical_to_baseline') else 'No change proposed'),
                    'blocked': 'Blocked by missing prerequisite',
                    'applied': 'Change applied', 'rejected': 'Candidate rejected'}.get(status, 'Inconclusive')
    if (reason is not None and reason not in REMEDIATION) or (status == 'analyzed' and reason is None):
        category = 'Inconclusive'
        action = 'Review the private receipt before retrying.'
    else:
        action = REMEDIATION.get(reason, STATUS_ACTION.get(status, 'Review the private receipt before retrying.'))
    events = analysis.get('events')
    cases = analysis.get('cases')
    coverage = f'{events} events, {cases} cases' if type(events) is int and type(cases) is int else 'unavailable'
    return f'{category}. Evidence coverage: {coverage}; capture completeness remains unverified. Next: {action}'
