"""Versioned, private audit summary for monitoring and the triage gate (#75); evidence only, no authority."""
from . import audit_decisions as decisions, audit_sampling as audit, triage_policy

PROFILE = 'audit-summary-v1'
OBSERVATIONS = ('triage-audit-monitor-observation-v1', 'triage-audit-monitor-composition-v1')
STATUSES = ('estimated', 'inconclusive', 'invalid')
FIELDS = ('schema_version profile policy_sha256 model_sha256 audit_receipt_sha256 design_profile estimand status reasons '
          'population_units drawn reviewed unreviewed review_status frame weights alert_only observed_false_negatives estimates '
          'missing_label_bounds decision_log limitations action promotion_authority identity_binding source_execution '
          'causal_attribution automatic_policy_update automatic_rollback publication_allowed summary_sha256')
FIXED = dict(action='observe_only', promotion_authority='none', identity_binding='caller_declared_identifiers_only',
             source_execution='unverified', causal_attribution='unestablished', automatic_policy_update=False,
             automatic_rollback=False, publication_allowed=False)


def _report(observation):
    """The audit estimate inside a monitor observation; composition views nest it one level down."""
    if not isinstance(observation, dict) or observation.get('profile') not in OBSERVATIONS:
        raise ValueError('triage audit monitor observation required')
    if any(observation.get(key) != value for key, value in FIXED.items()):
        raise ValueError('audit observation claims authority or lacks its limits')
    report = observation['audit_report']
    if observation['profile'] == OBSERVATIONS[1]:
        report = report['audit_report']
    if report.get('receipt_sha256') != observation['audit_receipt_sha256']:
        raise ValueError('audit estimate belongs to another receipt')
    return report


def _log(report, decision_report):
    if decision_report is None:
        return dict(status='not_supplied', head_sha256=None), ['decision_log_not_supplied']
    audit._fields(decision_report, 'schema_version profile receipt_sha256 status reason entries budget_changes '
                  'adaptive_decisions late_entries head_sha256 design_based_estimates_supported limitations '
                  'publication_allowed automatic_policy_update', 'invalid audit decision report')
    if (decision_report['profile'] != decisions.REPORT_PROFILE or decision_report['status'] not in ('logged_before_labels', 'invalid')
            or decision_report['design_based_estimates_supported'] != (decision_report['status'] != 'invalid')
            or decision_report['publication_allowed'] is not False or decision_report['automatic_policy_update'] is not False):
        raise ValueError('invalid audit decision report')
    if decision_report['receipt_sha256'] != report['receipt_sha256']:
        raise ValueError('audit decision report belongs to another receipt')
    return dict(status=decision_report['status'], head_sha256=decision_report['head_sha256'],
                entries=decision_report['entries']), list(decision_report['limitations'])


def summarize(observation, decision_report=None):
    """Compact the audit estimate of a monitor observation, optionally with its pre-label decision-log report.

    A decision made at or after the first label makes the summary ``invalid`` and drops every estimate and
    bound. Without a log the summary keeps its estimates and carries ``decision_log_not_supplied``."""
    report = _report(observation)
    log, limits = _log(report, decision_report)
    invalid = log['status'] == 'invalid'
    reasons = report.get('reasons', [report['reason']] if report.get('reason') else [])
    drawn = report.get('drawn', report.get('sample_size'))
    # Fixed and stratified reports lack some Poisson fields; an unreported field stays null, never zero.
    status_counts = report.get('review_status')
    reviewed = status_counts['reviewed'] if status_counts else report['reviewed']
    false_negatives = report.get('observed_false_negatives')
    body = dict(schema_version=1, profile=PROFILE, policy_sha256=observation['policy_sha256'],
                model_sha256=observation['model_sha256'], audit_receipt_sha256=report['receipt_sha256'],
                design_profile=report.get('source_estimate_profile', report['profile']), estimand=report['estimand'],
                status='invalid' if invalid else report['status'],
                reasons=['decision_at_or_after_first_label'] if invalid else list(reasons),
                population_units=report['population_units'], drawn=drawn, reviewed=reviewed, unreviewed=drawn - reviewed,
                review_status=status_counts, frame=report.get('frame'), weights=report.get('weights'),
                alert_only=report.get('alert_only'),
                observed_false_negatives=None if false_negatives is None else len(false_negatives),
                estimates=None if invalid else report['estimates'],
                missing_label_bounds=None if invalid else report.get('missing_label_bounds'), decision_log=log,
                limitations=list(dict.fromkeys([*report['limitations'], *limits])), **FIXED)
    return dict(body, summary_sha256=audit._digest(body))


def check(summary, policy):
    """Verify a summary is intact and bound to this triage policy; monitoring and the gate share this check."""
    audit._fields(summary, FIELDS, 'invalid audit summary')
    body = {key: value for key, value in summary.items() if key != 'summary_sha256'}
    triage_policy.check_policy(policy)
    if (type(summary['schema_version']) is not int or summary['schema_version'] != 1 or summary['profile'] != PROFILE
            or summary['status'] not in STATUSES or audit._digest(body) != summary['summary_sha256']
            or any(summary[key] != value for key, value in FIXED.items())
            or (summary['estimates'] is not None) != (summary['status'] == 'estimated')
            or not isinstance(summary['decision_log'], dict)
            or (summary['status'] == 'invalid') != (summary['decision_log'].get('status') == 'invalid')):
        raise ValueError('audit summary is altered or inconsistent')
    if (summary['policy_sha256'] != triage_policy.policy_sha256(policy) or policy['model_sha256'] is None
            or summary['model_sha256'] != policy['model_sha256']):
        raise ValueError('audit summary policy or model differs')
    return summary
