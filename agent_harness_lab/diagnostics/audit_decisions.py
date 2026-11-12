"""Pre-label log of audit budget changes and adaptive sampling decisions (#75), with no authority claims."""
from . import audit_sampling as audit

REPORT_PROFILE = 'audit-decision-log-report-v1'
KINDS = dict(budget_change='kind time prev_sha256 from_budget to_budget',
             adaptive_sampling='kind time prev_sha256 from_plan_sha256 to_plan_sha256')
MAX_ENTRIES = 256
LIMITATIONS = ('log_times_self_declared', 'first_label_time_declared_not_verified', 'log_completeness_unverified',
               'initial_budget_and_plan_unauthenticated')


def entry_sha256(entry):
    """Each entry links to the hash of the one before it; the first links to ``None``."""
    return audit.anchor_entry_sha256(entry)


def _entry(raw):
    if not isinstance(raw, dict) or raw.get('kind') not in KINDS:
        raise ValueError('audit decision entry kind required')
    audit._fields(raw, KINDS[raw['kind']], 'invalid audit decision entry')
    moment = audit._time(raw['time'])
    audit._sha(raw['prev_sha256'], True)
    if raw['kind'] == 'budget_change':
        if any(type(raw[k]) is not int or raw[k] < 1 for k in ('from_budget', 'to_budget')):
            raise ValueError('positive integer audit budgets required')
    else:
        audit._sha(raw['from_plan_sha256']); audit._sha(raw['to_plan_sha256'])
    return moment


def check(receipt, log, first_label_at, trusted_heads=None):
    """Verify a logged history ends at the receipt's budget and plan, and precedes the first label.

    A decision at or after ``first_label_at`` makes the result ``invalid`` for design-based inference.
    Malformed, unchained or discontinuous logs are refused."""
    audit._receipt(receipt, trusted_heads)
    first = audit._time(first_label_at)
    if not isinstance(log, list) or len(log) > MAX_ENTRIES:
        raise ValueError('bounded audit decision log required')
    prev, last, budget, plan, late = None, None, None, None, []
    for index, raw in enumerate(log):
        moment = _entry(raw)
        if raw['prev_sha256'] != prev or (last and moment < last):
            raise ValueError('audit decision log chain or time order broken')
        if raw['kind'] == 'budget_change':
            if budget is not None and raw['from_budget'] != budget: raise ValueError('audit budget history is discontinuous')
            budget = raw['to_budget']
        else:
            if plan is not None and raw['from_plan_sha256'] != plan: raise ValueError('audit plan history is discontinuous')
            plan = raw['to_plan_sha256']
        if moment >= first: late.append(index)
        prev, last = entry_sha256(raw), moment
    if budget is not None and budget != receipt['review_budget'] or plan is not None and plan != receipt['plan_sha256']:
        raise ValueError('audit decision log does not end at the receipt budget and plan')
    return dict(schema_version=1, profile=REPORT_PROFILE, receipt_sha256=receipt['receipt_sha256'],
                status='invalid' if late else 'logged_before_labels', reason='decision_at_or_after_first_label' if late else None,
                entries=len(log), budget_changes=sum(r['kind'] == 'budget_change' for r in log),
                adaptive_decisions=sum(r['kind'] == 'adaptive_sampling' for r in log), late_entries=late,
                head_sha256=prev, design_based_estimates_supported=not late, limitations=list(LIMITATIONS),
                publication_allowed=False, automatic_policy_update=False)
