"""Frozen multi-class diagnostic triage policy (schema v2); validation role only.

Fitting, calibration and threshold selection happen elsewhere. This module only
checks a frozen, human-preregistered policy on held-out declared roots: it never
selects or adjusts a threshold, refuses roots the policy declares as fitting,
calibration or threshold-tuning roots, and issues no certification. Schema v1
binary policies stay in ``tools.benchmark_models.evaluate_triage_policy``.
"""
import hashlib
import json
import math

POLICY_PROFILE = 'diagnostic-triage-policy-v2'
REPORT_PROFILE = 'diagnostic-triage-evaluation-v2'
REPORT_PROFILE_V3 = 'diagnostic-triage-evaluation-v3'
SEVERITIES = ('severe', 'normal', 'none')
COSTS = ('false_alert', 'wrong_class', 'missed_alert', 'severe_miss', 'review')
ROLE_ROOTS = ('fit_roots_sha256', 'calibration_roots_sha256', 'threshold_tuning_roots_sha256')
POLICY_FIELDS = frozenset(('schema_version', 'profile', 'classes', 'costs', 'review_capacity_roots', 'abstention',
                           'min_coverage', 'max_severe_misses', 'threshold_provenance', 'model_sha256',
                           'calibrator_sha256', *ROLE_ROOTS))
CLASS_FIELDS = frozenset(('severity', 'threshold', 'min_precision', 'min_recall'))
ABSTAINED = 'abstained'
CONFLICT = 'conflicting_root_decisions'
LIMITATIONS = ('independent_validation_not_verified', 'threshold_preregistration_unverified',
               'label_authority_unverified', 'root_independence_unverified')


def root_sha256(root):
    """Hash declared root IDs; reports and policy role lists never carry raw IDs."""
    return hashlib.sha256(root.encode('utf-8')).hexdigest()


def policy_sha256(policy):
    return hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _number(value, upper: float | None = 1):
    if type(value) is int:
        return 0 <= value and (upper is None or value <= upper)
    return type(value) is float and math.isfinite(value) and 0 <= value and (upper is None or value <= upper)


def _count(value):
    return type(value) is int and value >= 0


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def check_policy(policy):
    """Validate a frozen v2 policy; return its class order and forbidden evaluation roots."""
    if not isinstance(policy, dict) or set(policy) != POLICY_FIELDS:
        raise ValueError('invalid triage policy v2 schema')
    if type(policy['schema_version']) is not int or policy['schema_version'] != 2 or policy['profile'] != POLICY_PROFILE:
        raise ValueError('invalid triage policy v2 schema')
    if policy['threshold_provenance'] != 'human_preregistered':
        raise ValueError('v2 validation accepts only human-preregistered thresholds; it never selects them')
    classes = policy['classes']
    if not isinstance(classes, dict) or len(classes) < 2:
        raise ValueError('triage policy v2 requires at least two declared classes')
    for name, spec in classes.items():
        if not isinstance(name, str) or not name or len(name) > 128 or name != name.strip() or ':' in name or name.startswith(ABSTAINED):
            raise ValueError('invalid triage class name')
        if not isinstance(spec, dict) or set(spec) != CLASS_FIELDS or spec['severity'] not in SEVERITIES:
            raise ValueError('invalid triage class policy')
        if spec['threshold'] is None:
            raise ValueError('frozen per-class threshold required; v2 validation never selects thresholds')
        if not all(_number(spec[key]) for key in ('threshold', 'min_precision', 'min_recall')):
            raise ValueError('invalid triage class constraint')
    costs = policy['costs']
    if not isinstance(costs, dict) or set(costs) != set(COSTS) or not all(_number(costs[k], None) for k in COSTS):
        raise ValueError('explicit finite nonnegative cost for every error type required')
    abstention = policy['abstention']
    if not isinstance(abstention, dict) or set(abstention) != {'min_margin'} or not _number(abstention['min_margin']):
        raise ValueError('invalid triage abstention rule')
    if not _count(policy['review_capacity_roots']) or not _count(policy['max_severe_misses']):
        raise ValueError('review capacity and severe-miss maximum must be nonnegative integers')
    if not _number(policy['min_coverage']):
        raise ValueError('invalid triage coverage constraint')
    for key in ('model_sha256', 'calibrator_sha256'):
        if policy[key] is not None and not _sha(policy[key]):
            raise ValueError('model and calibrator identities must be SHA-256 or null')
    forbidden = {}
    for key in ROLE_ROOTS:
        values = policy[key]
        if not isinstance(values, list) or not all(_sha(v) for v in values) or len(set(values)) != len(values):
            raise ValueError('role root lists must contain unique SHA-256 root hashes')
        for value in values:
            forbidden.setdefault(value, key)
    return tuple(sorted(classes)), forbidden


def _row_decision(scores, names, classes, margin):
    """Return a frozen-threshold class or an abstention reason; ties always abstain."""
    ranked = sorted(names, key=lambda name: -scores[name])
    top, second = scores[ranked[0]], scores[ranked[1]]
    if top == second:
        return None, 'tied_top_classes'
    if top < classes[ranked[0]]['threshold']:
        return None, 'below_class_threshold'
    if top - second < margin:
        return None, 'margin_below_minimum'
    return ranked[0], None


def _aggregate(observations, policy, names, forbidden):
    """Single pass over rows; per-root state is the only retained data."""
    classes, margin = policy['classes'], policy['abstention']['min_margin']
    severe = [name for name in names if classes[name]['severity'] == 'severe']
    roots = {}
    for row in observations:
        if not isinstance(row, dict) or row.get('split') != 'heldout':
            raise ValueError('triage validation requires heldout rows')
        root = row.get('root_case_id')
        if not isinstance(root, str) or not root or len(root) > 256:
            raise ValueError('triage validation requires declared root IDs')
        label, scores = row.get('label'), row.get('scores')
        if label is not None and (not isinstance(label,str) or label not in classes):
            raise ValueError('label outside the declared triage taxonomy')
        if not isinstance(scores, dict) or set(scores) != set(names):
            raise ValueError('class scores must cover exactly the declared taxonomy')
        if not all(_number(value) for value in scores.values()):
            raise ValueError('invalid triage class score')
        digest = root_sha256(root)
        if digest in forbidden:
            raise ValueError(f'evaluated root appears in policy {forbidden[digest]}; validation roots must be untouched')
        predicted, reason = _row_decision(scores, names, classes, margin)
        state = roots.get(digest)
        if state is None:
            state = roots[digest] = [label, set(), 0.0]
        elif state[0] != label:
            raise ValueError('conflicting labels within a declared validation root')
        state[1].add(predicted if reason is None else ABSTAINED+':'+reason)
        state[2] = max([state[2], *(scores[name] for name in severe)])
    if not roots:
        raise ValueError('empty triage validation input')
    return roots


def evaluate(policy, observations, *, report_version=2):
    """Describe a frozen v2 policy on held-out roots; supplied labels grant no authority.

    ``observations`` may be any iterable (including a generator) of row dicts with
    ``root_case_id``, ``split: heldout``, ``label`` (declared class or None) and
    ``scores`` (one score in [0, 1] per declared class).
    """
    if type(report_version) is not int or report_version not in (2, 3):
        raise ValueError('unsupported triage report version')
    names, forbidden = check_policy(policy)
    classes, costs = policy['classes'], policy['costs']
    roots = _aggregate(observations, policy, names, forbidden)
    abstained = []
    # Replace per-root aggregation state in place with the root decision.
    for digest, (label, outcomes, risk) in roots.items():
        outcome = next(iter(outcomes)) if len(outcomes) == 1 else ABSTAINED+':'+CONFLICT
        if outcome.startswith(ABSTAINED+':'):
            roots[digest] = (label, None, outcome.split(':', 1)[1])
            abstained.append((-risk, digest))
        else:
            roots[digest] = (label, outcome, None)
    # Label-free queue: highest severe-class score first, then hash order.
    abstained.sort()
    reviewed = {digest for _, digest in abstained[:policy['review_capacity_roots']]}
    confusion = {label: dict.fromkeys((*names, ABSTAINED), 0) for label in names}
    errors = dict.fromkeys(COSTS, 0)
    by_reason, severe_roots = {}, []
    severe_detail = dict(predicted_non_severe=0, unreviewed_abstention=0)
    labeled = decided = unknown = 0
    report_decisions = []
    for digest in sorted(roots):
        label, predicted, reason = roots[digest]
        review = digest in reviewed
        report_decisions.append(dict(root_sha256=digest, value=predicted, abstain=predicted is None,
                                     abstention_reason=reason, review=review))
        if predicted is None:
            by_reason[reason] = by_reason.get(reason, 0)+1
            errors['review'] += review
        if label is None:
            unknown += 1
            continue
        labeled += 1
        confusion[label][ABSTAINED if predicted is None else predicted] += 1
        truth = classes[label]['severity']
        if predicted is None:
            kind = None if review or truth == 'none' else 'severe_miss' if truth == 'severe' else 'missed_alert'
            if kind == 'severe_miss':
                severe_detail['unreviewed_abstention'] += 1
        else:
            decided += 1
            guess = classes[predicted]['severity']
            if predicted == label:
                kind = None
            elif truth == 'severe' and guess != 'severe':
                kind = 'severe_miss'
                severe_detail['predicted_non_severe'] += 1
            elif truth == 'none' and guess != 'none':
                kind = 'false_alert'
            elif truth == 'normal' and guess == 'none':
                kind = 'missed_alert'
            else:
                kind = 'wrong_class'
        if kind is not None:
            errors[kind] += 1
        if kind == 'severe_miss':
            severe_roots.append(digest)
    per_class, failed, missing = {}, [], []
    for name in names:
        row = confusion[name]
        tp = row[name]
        fn = sum(row[other] for other in names if other != name)
        fp = sum(confusion[other][name] for other in names if other != name)
        held = row[ABSTAINED]
        support = tp+fn+held
        metrics = dict(severity=classes[name]['severity'], roots=support, tp=tp, fp=fp, fn=fn,
                       tn=decided-tp-fp-fn, abstained=held,
                       precision=tp/(tp+fp) if tp+fp else None, recall=tp/support if support else None)
        per_class[name] = metrics
        if not support:
            missing.append(f'class:{name}:no_validation_roots')
        for metric in ('precision', 'recall'):
            if metrics[metric] is None:
                missing.append(f'class:{name}:{metric}_undefined')
            elif metrics[metric] < classes[name]['min_'+metric]:
                failed.append(f'class:{name}:{metric}_below_minimum')
    # V2 retains its original labeled-root denominator for saved-report replay.
    # V3 measures decisions on all declared roots, including unlabeled roots.
    if report_version == 2:
        coverage = decided/labeled if labeled else None
    else:
        coverage = (len(roots)-len(abstained))/len(roots)
    if coverage is None:
        missing.append('coverage_undefined')
    elif coverage < policy['min_coverage']:
        failed.append('coverage_below_minimum')
    if len(severe_roots) > policy['max_severe_misses']:
        failed.append('severe_misses_above_maximum')
    overflow = max(0, len(abstained)-policy['review_capacity_roots'])
    if overflow:
        failed.append('review_capacity_exceeded')
    try:
        total_cost = sum(errors[k]*costs[k] for k in COSTS)
        if type(total_cost) is float and not math.isfinite(total_cost): total_cost = None
    except OverflowError:
        total_cost = None
    if total_cost is None:
        missing.append('cost_total_unrepresentable')
    return dict(schema_version=report_version, profile=REPORT_PROFILE if report_version == 2 else REPORT_PROFILE_V3, role='validate',
        status='rejected' if failed else 'insufficient_evidence', reasons=failed+missing+list(LIMITATIONS),
        policy_sha256=policy_sha256(policy), model_sha256=policy['model_sha256'],
        calibrator_sha256=policy['calibrator_sha256'], unit='declared_root',
        labeled_roots=labeled, unknown_roots=unknown, decided_labeled_roots=decided, coverage=coverage,
        classes=per_class, confusion=confusion,
        abstentions=dict(total=len(abstained), by_reason=dict(sorted(by_reason.items())), routed_to_review=len(reviewed),
                         unreviewed=overflow, review_capacity_roots=policy['review_capacity_roots'],
                         capacity_overflow=overflow, queue_order='max_severe_class_score_then_root_hash'),
        severe_misses=dict(count=len(severe_roots), maximum=policy['max_severe_misses'], **severe_detail,
                           roots_sha256=sorted(severe_roots)),
        costs=dict(declared=dict(costs), counts=errors, total=total_cost),
        decisions=report_decisions, decision_threshold_source='frozen_policy',
        threshold_authority='human_preregistered_unverified', threshold_selection='refused_in_validation',
        root_independence='unverified', label_authority='unverified', qualification='descriptive_only',
        certification='not_issued', automatic_promotion=False, publication_allowed=False)
