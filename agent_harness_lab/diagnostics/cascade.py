"""Two-stage diagnostic cascade evaluation on held-out declared roots (#14); validation role only.

The first prediction is preserved next to the final one, so corrections are never reported
without the errors the second stage introduced. A frozen human policy supplies every limit.
This module selects no threshold, grants no promotion authority and never issues a
"beneficial" verdict: its best status is ``insufficient_evidence``.
"""
import hashlib
import json
import math

POLICY_PROFILE = 'diagnostic-cascade-policy-v1'
REPORT_PROFILE = 'diagnostic-cascade-evaluation-v1'
POLICY_FIELDS = frozenset(('schema_version', 'profile', 'classes', 'severe_classes', 'min_net_errors_removed',
                           'max_introduced_errors', 'max_severe_misses', 'max_review_roots', 'max_total_cost'))
STAGES = ('first', 'second')
RESOURCES = ('cost', 'latency_seconds')
LIMITATIONS = ('independent_validation_not_verified', 'policy_preregistration_unverified',
               'label_authority_unverified', 'root_independence_unverified')


def _count(value):
    return type(value) is int and value >= 0


def _amount(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def check_policy(policy):
    if not isinstance(policy, dict) or set(policy) != POLICY_FIELDS:
        raise ValueError('invalid cascade policy schema')
    if type(policy['schema_version']) is not int or policy['schema_version'] != 1 or policy['profile'] != POLICY_PROFILE:
        raise ValueError('invalid cascade policy schema')
    classes, severe = policy['classes'], policy['severe_classes']
    if not isinstance(classes, list) or len(classes) < 2 or len(set(classes)) != len(classes) \
            or not all(isinstance(c, str) and c and len(c) <= 128 for c in classes):
        raise ValueError('cascade policy requires at least two unique declared classes')
    if not isinstance(severe, list) or not set(severe) <= set(classes):
        raise ValueError('severe classes must be declared classes')
    if not (_count(policy['min_net_errors_removed']) and policy['min_net_errors_removed'] >= 1):
        raise ValueError('a cascade must be required to remove at least one net error')
    if not all(_count(policy[k]) for k in ('max_introduced_errors', 'max_severe_misses', 'max_review_roots')):
        raise ValueError('cascade limits must be nonnegative integers')
    if policy['max_total_cost'] is not None and not _amount(policy['max_total_cost']):
        raise ValueError('invalid cascade cost limit')


def _outcome(prediction, label):
    return 'abstained' if prediction is None else 'correct' if prediction == label else 'wrong'


def evaluate(policy, observations):
    """Describe the cascade on held-out roots; supplied labels and costs grant no authority.

    Each row has ``root_case_id``, ``split: heldout``, ``label`` (declared class or None) and
    ``first`` (declared class or None for abstention; the key is required, a missing one is
    unknown capture and is refused). A present ``second`` key means the second
    stage ran (a class, or None for abstention); its decided answer replaces the first answer,
    and an abstaining or skipped second stage leaves the first answer. Optional
    ``{first,second}_{cost,latency_seconds}`` are caller-measured nonnegative numbers.
    One row per root.
    """
    check_policy(policy)
    classes, severe = set(policy['classes']), set(policy['severe_classes'])
    seen, labeled, unknown, invoked = set(), [], 0, 0
    review = dict(first=0, final=0)
    totals = dict.fromkeys(RESOURCES, 0)
    unmeasured = set()
    for row in observations:
        if not isinstance(row, dict) or row.get('split') != 'heldout':
            raise ValueError('cascade validation requires heldout rows')
        root = row.get('root_case_id')
        if not isinstance(root, str) or not root or len(root) > 256 or root in seen:
            raise ValueError('cascade validation requires one row per declared root')
        seen.add(root)
        if 'first' not in row:
            raise ValueError('cascade validation requires the first-stage answer; omission is not abstention')
        label, first = row.get('label'), row['first']
        ran = 'second' in row
        second = row.get('second')
        if not all(v is None or isinstance(v, str) and v in classes for v in (label, first, second)):
            raise ValueError('label or prediction outside the declared cascade taxonomy')
        final = second if ran and second is not None else first
        invoked += ran
        review['first'] += first is None
        review['final'] += final is None
        for stage in STAGES[:1 + ran]:
            for key in RESOURCES:
                value = row.get(f'{stage}_{key}')
                if value is None:
                    unmeasured.add(key)
                elif not _amount(value):
                    raise ValueError('invalid measured cascade resource')
                else:
                    totals[key] += value
        if label is None:
            unknown += 1
        else:
            labeled.append((label, first, final))
    if not seen:
        raise ValueError('empty cascade validation input')
    firsts = dict.fromkeys(('correct', 'wrong', 'abstained'), 0)
    finals = dict(firsts)
    moves = dict.fromkeys(('corrected_errors', 'introduced_errors_from_correct', 'introduced_errors_from_abstention',
                           'resolved_abstentions'), 0)
    severe_misses = dict(first=0, final=0)
    for label, first, final in labeled:
        before, after = _outcome(first, label), _outcome(final, label)
        firsts[before] += 1
        finals[after] += 1
        moves['corrected_errors'] += before == 'wrong' and after == 'correct'
        moves['resolved_abstentions'] += before == 'abstained' and after == 'correct'
        moves['introduced_errors_from_correct'] += before == 'correct' and after == 'wrong'
        moves['introduced_errors_from_abstention'] += before == 'abstained' and after == 'wrong'
        if label in severe:
            severe_misses['first'] += before != 'correct'
            severe_misses['final'] += after != 'correct'
    introduced = moves['introduced_errors_from_correct'] + moves['introduced_errors_from_abstention']
    net = firsts['wrong'] - finals['wrong']
    total_cost = None if 'cost' in unmeasured else totals['cost']
    failed = []
    if labeled:  # without labels the label-derived metrics are unknown, never zero-valued failures
        if net < policy['min_net_errors_removed']:
            failed.append('net_errors_removed_below_minimum')
        if introduced > policy['max_introduced_errors']:
            failed.append('introduced_errors_above_maximum')
        if severe_misses['final'] > policy['max_severe_misses']:
            failed.append('severe_misses_above_maximum')
        if severe_misses['final'] > severe_misses['first']:
            failed.append('severe_misses_increased')
    if review['final'] > policy['max_review_roots']:
        failed.append('review_roots_above_maximum')
    if policy['max_total_cost'] is not None and total_cost is not None and total_cost > policy['max_total_cost']:
        failed.append('total_cost_above_maximum')
    missing = [f'{key}_unmeasured' for key in RESOURCES if key in unmeasured]
    if not labeled:
        missing.append('no_labeled_roots')
    return dict(
        schema_version=1, profile=REPORT_PROFILE, role='validate',
        status='rejected' if failed else 'insufficient_evidence', reasons=failed + missing + list(LIMITATIONS),
        policy_sha256=hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        unit='declared_root', roots=len(seen), labeled_roots=len(labeled), unknown_roots=unknown,
        second_stage_roots=invoked, final_answer_rule='second_decided_overrides_first',
        first=dict(firsts, severe_misses=severe_misses['first']), final=dict(finals, severe_misses=severe_misses['final']),
        transitions=dict(moves, introduced_errors=introduced, net_errors_removed=net),
        review_roots=review,
        resources=dict(total_cost=total_cost, total_latency_seconds=None if 'latency_seconds' in unmeasured else totals['latency_seconds']),
        first_prediction_preserved=True, beneficial_claim='not_issued', qualification='descriptive_only',
        certification='not_issued', automatic_promotion=False, publication_allowed=False,
        root_independence='unverified', label_authority='unverified')
