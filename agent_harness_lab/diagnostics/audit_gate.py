"""Preregistered requirement gate over the versioned audit summary (#75); descriptive evidence, no authority.

A caller declares, before reading any label, how many reviewed draws an audit needs, the
largest acceptable missed-incident rate and optionally the smallest acceptable triage recall.
``evaluate`` reads one ``audit-summary-v1`` bound to a triage policy and answers
``requirement_met``, ``requirement_not_met``, ``inconclusive`` or ``invalid``. A limit is met
only when the whole 95% interval clears it and not met only when the whole interval breaks it,
so an interval that straddles the limit, an undefined recall, pending or nonresponding reviews
and a missing or too small review count stay ``inconclusive``. Missing-label bounds are never
used to pass or fail a requirement. The gate selects no threshold, updates no policy and
promotes nothing, and its result carries the same limitations as the summary.
"""
from . import audit_sampling as audit, audit_summary as summary_api, triage_policy

PROFILE = 'audit-gate-v1'
REQUIREMENT_PROFILE = 'audit-gate-requirement-v1'
STATUSES = ('requirement_met', 'requirement_not_met', 'inconclusive', 'invalid')
REQUIREMENT_FIELDS = 'schema_version profile min_reviewed max_missed_incident_rate min_triage_recall require_decision_log'
FIXED = dict(action='observe_only', promotion_authority='none', certification='not_issued',
             automatic_policy_update=False, automatic_rollback=False, publication_allowed=False)
LIMITATIONS = ('requirement_preregistration_unverified', 'intervals_conditional_on_declared_design')


def requirement_sha256(requirement):
    """Hash of a validated requirement; the gate result carries it so a changed requirement is visible."""
    audit._fields(requirement, REQUIREMENT_FIELDS, 'invalid audit requirement')
    if (requirement['schema_version'] != 1 or type(requirement['schema_version']) is not int
            or requirement['profile'] != REQUIREMENT_PROFILE
            or type(requirement['min_reviewed']) is not int or requirement['min_reviewed'] < 1
            or not triage_policy._number(requirement['max_missed_incident_rate'])
            or not (requirement['min_triage_recall'] is None or triage_policy._number(requirement['min_triage_recall']))
            or type(requirement['require_decision_log']) is not bool):
        raise ValueError('invalid audit requirement')
    return audit._digest(requirement)


def _interval(value):
    """A summary is sealed, not authenticated, so its interval is validated before a verdict reads it."""
    if (not isinstance(value, list) or len(value) != 2 or not all(triage_policy._number(limit) for limit in value)
            or value[0] > value[1]):
        raise ValueError('audit summary estimate interval malformed')
    return value


def _limits(estimates):
    """The missed-incident rate and triage recall intervals; Poisson and finite designs name them differently."""
    try:
        rate = estimates['missed_incidents']['rate'] if 'missed_incidents' in estimates else estimates['missed_incident_rate']
        recall = estimates['triage_recall']
        rate = _interval(rate['ci95'])
        recall = None if recall is None or recall['estimate'] is None else _interval(recall['ci95'])
    except (KeyError, TypeError) as error:
        raise ValueError('audit summary estimates malformed') from error
    return rate, recall


def _verdict(interval, limit, upper):
    """``upper``: the limit is a maximum, met when the interval ends at or below it, broken when it starts above it."""
    low, high = interval
    if upper:
        return 'met' if high <= limit else 'not_met' if low > limit else 'inconclusive'
    return 'met' if low >= limit else 'not_met' if high < limit else 'inconclusive'


def evaluate(summary, policy, requirement):
    """Judge one checked audit summary against a declared requirement; refuses an altered or foreign summary."""
    summary_api.check(summary, policy)
    digest = requirement_sha256(requirement)
    log = summary['decision_log']['status']
    criteria, reasons = None, []
    if summary['status'] == 'invalid':
        status, reasons = 'invalid', list(summary['reasons'])
    else:
        reasons = list(summary['reasons']) if summary['status'] == 'inconclusive' else []
        if requirement['require_decision_log'] and log == 'not_supplied':
            reasons.append('decision_log_not_supplied')
        if summary['reviewed'] < requirement['min_reviewed']:
            reasons.append('reviewed_below_minimum')
        status = 'inconclusive'
        if not reasons:
            rate, recall = _limits(summary['estimates'])
            criteria = dict(missed_incident_rate=dict(maximum=requirement['max_missed_incident_rate'], ci95=rate,
                                                      verdict=_verdict(rate, requirement['max_missed_incident_rate'], True)))
            if requirement['min_triage_recall'] is not None:
                criteria['triage_recall'] = dict(minimum=requirement['min_triage_recall'], ci95=recall,
                                                 verdict='inconclusive' if recall is None else
                                                 _verdict(recall, requirement['min_triage_recall'], False))
            for name, item in criteria.items():
                if item['ci95'] is None:
                    reasons.append(f'{name}_undefined')
                elif item['verdict'] != 'met':
                    reasons.append(f'{name}_not_met' if item['verdict'] == 'not_met' else f'{name}_interval_straddles_limit')
            verdicts = {item['verdict'] for item in criteria.values()}
            status = ('requirement_not_met' if 'not_met' in verdicts
                      else 'requirement_met' if verdicts == {'met'} else 'inconclusive')
    body = dict(schema_version=1, profile=PROFILE, summary_sha256=summary['summary_sha256'], requirement_sha256=digest,
                policy_sha256=summary['policy_sha256'], model_sha256=summary['model_sha256'],
                audit_receipt_sha256=summary['audit_receipt_sha256'], design_profile=summary['design_profile'],
                estimand=summary['estimand'], status=status, reasons=reasons, reviewed=summary['reviewed'],
                population_units=summary['population_units'], criteria=criteria,
                limitations=list(dict.fromkeys([*summary['limitations'], *LIMITATIONS])), **FIXED)
    return body
