"""Frozen multi-class diagnostic triage policy (schema v2): validation and threshold selection.

Fitting and calibration happen elsewhere. ``evaluate`` only checks a frozen policy
on held-out declared roots: it never selects or adjusts a threshold, refuses roots
the policy declares as fitting, calibration or threshold-tuning roots, and issues
no certification. ``select_thresholds`` is the one separate role that may choose
thresholds, and only from declared tuning rows. Schema v1 binary policies stay in
``tools.benchmark_models.evaluate_triage_policy``.
"""
from copy import deepcopy
import hashlib
import json
import math

POLICY_PROFILE = 'diagnostic-triage-policy-v2'
REPORT_PROFILE = 'diagnostic-triage-evaluation-v2'
REPORT_PROFILE_V3 = 'diagnostic-triage-evaluation-v3'
REPORT_PROFILE_V4 = 'diagnostic-triage-evaluation-v4'
REPORT_PROFILE_V5 = 'diagnostic-triage-evaluation-v5'
REPORT_PROFILE_V6 = 'diagnostic-triage-evaluation-v6'
REPORT_PROFILE_V7 = 'diagnostic-triage-evaluation-v7'
REPORT_PROFILE_V8 = 'diagnostic-triage-evaluation-v8'
REPORT_PROFILE_V9 = 'diagnostic-triage-evaluation-v9'
SELECTION_PROFILE = 'diagnostic-triage-threshold-selection-v1'
SELECTED = 'selected_on_tuning_roots'
BIN_EDGES = (0.0, 0.2, 0.4, 0.6, 0.8)  # fixed descriptive grid, never a tuned or accepted value
MIN_BIN_ROOTS = 10  # below this a reliability bin is reported as sparse, not as evidence
SEVERITIES = ('severe', 'normal', 'none')
COSTS = ('false_alert', 'wrong_class', 'missed_alert', 'severe_miss', 'review')
ROLE_ROOTS = ('fit_roots_sha256', 'calibration_roots_sha256', 'threshold_tuning_roots_sha256')
POLICY_FIELDS = frozenset(('schema_version', 'profile', 'classes', 'costs', 'review_capacity_roots', 'abstention',
                           'min_coverage', 'max_severe_misses', 'threshold_provenance', 'model_sha256',
                           'calibrator_sha256', *ROLE_ROOTS))
SCOPE_FIELDS = frozenset(('corpus_sha256', 'min_calibration_roots'))  # accepted only by report v6 and later; earlier callers refuse them
CLASS_CALIBRATION_FIELDS = frozenset(('min_calibration_roots_per_class', 'calibration_class_roots_sha256'))  # report v7 only
CLASS_FIELDS = frozenset(('severity', 'threshold', 'min_precision', 'min_recall'))
ABSTAINED = 'abstained'
CONFLICT = 'conflicting_root_decisions'
LIMITATIONS = ('independent_validation_not_verified', 'threshold_preregistration_unverified',
               'label_authority_unverified', 'root_independence_unverified')
SELECTION_LIMITATIONS = ('same_sample_estimates_only', 'target_preregistration_unverified',
                         'label_authority_unverified', 'root_independence_unverified')
SPLIT_REFUSAL = {'heldout': 'triage validation requires heldout rows', 'tuning': 'threshold selection requires tuning rows'}


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


def check_policy(policy, *, require_disjoint_roles=False, require_scope=False, require_class_calibration=False,
                 allow_selected=False, selecting=False):
    """Validate a frozen v2 policy; return its class order and forbidden evaluation roots.

    ``allow_selected`` also accepts thresholds selected on declared tuning roots. ``selecting`` validates the
    draft ``select_thresholds`` fills in: thresholds unset, no tuning roots declared yet.
    """
    expected = POLICY_FIELDS | (SCOPE_FIELDS if require_scope else frozenset()) | (CLASS_CALIBRATION_FIELDS if require_class_calibration else frozenset())
    if not isinstance(policy, dict) or set(policy) != expected:
        raise ValueError('invalid triage policy v2 schema')
    if type(policy['schema_version']) is not int or policy['schema_version'] != 2 or policy['profile'] != POLICY_PROFILE:
        raise ValueError('invalid triage policy v2 schema')
    allowed = (SELECTED,) if selecting else ('human_preregistered', *((SELECTED,) if allow_selected else ()))
    if policy['threshold_provenance'] not in allowed:
        raise ValueError('v2 validation accepts only human-preregistered thresholds; it never selects them')
    classes = policy['classes']
    if not isinstance(classes, dict) or len(classes) < 2:
        raise ValueError('triage policy v2 requires at least two declared classes')
    for name, spec in classes.items():
        if not isinstance(name, str) or not name or len(name) > 128 or name != name.strip() or ':' in name or name.startswith(ABSTAINED):
            raise ValueError('invalid triage class name')
        if not isinstance(spec, dict) or set(spec) != CLASS_FIELDS or spec['severity'] not in SEVERITIES:
            raise ValueError('invalid triage class policy')
        if (spec['threshold'] is None) != selecting:
            raise ValueError('a threshold selection draft must leave every class threshold unset' if selecting
                             else 'frozen per-class threshold required; v2 validation never selects thresholds')
        if not all(_number(spec[key]) for key in ('min_precision', 'min_recall', *(() if selecting else ('threshold',)))):
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
    if require_scope and not (_sha(policy['corpus_sha256']) and _count(policy['min_calibration_roots'])):
        raise ValueError('corpus revision must be SHA-256 and the calibration-root minimum a nonnegative integer')
    forbidden = {}
    for key in ROLE_ROOTS:
        values = policy[key]
        if not isinstance(values, list) or not all(_sha(v) for v in values) or len(set(values)) != len(values):
            raise ValueError('role root lists must contain unique SHA-256 root hashes')
        for value in values:
            if require_disjoint_roles and value in forbidden:
                raise ValueError('declared fitting, calibration and threshold role roots overlap')
            forbidden.setdefault(value, key)
    if require_class_calibration:
        _check_class_calibration(policy, classes)
    if policy['threshold_provenance'] == SELECTED and selecting == bool(policy['threshold_tuning_roots_sha256']):
        raise ValueError('a selection draft declares no tuning roots yet' if selecting else 'a selected policy must declare its tuning roots')
    return tuple(sorted(classes)), forbidden


def _check_class_calibration(policy, classes):
    """Per-class calibration roots must be unique hashes drawn from the declared calibration roots, one class per root."""
    declared, groups = set(policy['calibration_roots_sha256']), policy['calibration_class_roots_sha256']
    if not _count(policy['min_calibration_roots_per_class']):
        raise ValueError('class calibration minimum must be a nonnegative integer')
    if not isinstance(groups, dict) or set(groups) != set(classes) or not all(isinstance(v, list) for v in groups.values()):
        raise ValueError('class calibration roots must be one list for every declared class')
    members = [value for values in groups.values() for value in values]
    if not all(_sha(v) and v in declared for v in members) or len(set(members)) != len(members):
        raise ValueError('class calibration roots must be unique hashes from the declared calibration roots, one class per root')


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


def _read_row(row, policy, names, forbidden, split, bind_model, bind_corpus):
    """Validate one observation of the given role and return its root hash, label and scores."""
    if not isinstance(row, dict) or row.get('split') != split:
        raise ValueError(SPLIT_REFUSAL[split])
    root = row.get('root_case_id')
    if not isinstance(root, str) or not root or len(root) > 256:
        raise ValueError('triage validation requires declared root IDs')
    label, scores = row.get('label'), row.get('scores')
    if label is not None and (not isinstance(label,str) or label not in policy['classes']):
        raise ValueError('label outside the declared triage taxonomy')
    if not isinstance(scores, dict) or set(scores) != set(names):
        raise ValueError('class scores must cover exactly the declared taxonomy')
    if not all(_number(value) for value in scores.values()):
        raise ValueError('invalid triage class score')
    if bind_model and row.get('model_sha256') != policy['model_sha256']:
        raise ValueError('observation model revision is outside the policy scope')
    if bind_corpus and row.get('corpus_sha256') != policy['corpus_sha256']:
        raise ValueError('observation corpus revision is outside the policy scope')
    digest = root_sha256(root)
    if digest in forbidden:
        raise ValueError(f'evaluated root appears in policy {forbidden[digest]}; validation roots must be untouched')
    return digest, label, scores


def _aggregate(observations, policy, names, forbidden, bind_model=False, sums=None, bind_corpus=False):
    """Single pass over rows; per-root state is the only retained data."""
    classes, margin = policy['classes'], policy['abstention']['min_margin']
    severe = [name for name in names if classes[name]['severity'] == 'severe']
    roots = {}
    for row in observations:
        digest, label, scores = _read_row(row, policy, names, forbidden, 'heldout', bind_model, bind_corpus)
        predicted, reason = _row_decision(scores, names, classes, margin)
        state = roots.get(digest)
        if state is None:
            state = roots[digest] = [label, set(), 0.0]
        elif state[0] != label:
            raise ValueError('conflicting labels within a declared validation root')
        state[1].add(predicted if reason is None else ABSTAINED+':'+reason)
        if sums is not None:
            total = sums.setdefault(digest, [0, dict.fromkeys(names, 0.0)])
            total[0] += 1
            for name in names:
                total[1][name] += scores[name]
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
    if type(report_version) is not int or report_version not in (2, 3, 4, 5, 6, 7, 8, 9):
        raise ValueError('unsupported triage report version')
    names, forbidden = check_policy(policy, require_disjoint_roles=report_version >= 4, require_scope=report_version >= 6,
                                  require_class_calibration=report_version >= 7, allow_selected=report_version >= 9)
    classes, costs = policy['classes'], policy['costs']
    if report_version >= 4 and policy['model_sha256'] is None:
        raise ValueError('v4 and later require a policy bound to a model revision')
    sums = {} if report_version >= 5 else None
    roots = _aggregate(observations, policy, names, forbidden, bind_model=report_version >= 4, sums=sums,
                       bind_corpus=report_version >= 6)
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
    # V3 to V5 measure decisions on all declared roots, including unlabeled roots.
    if report_version == 2:
        coverage = decided/labeled if labeled else None
    else:
        coverage = (len(roots)-len(abstained))/len(roots)
    if coverage is None:
        missing.append('coverage_undefined')
    elif coverage < policy['min_coverage']:
        failed.append('coverage_below_minimum')
    # A declared calibrator fitted on too few declared roots cannot support the frozen decisions.
    if report_version >= 6 and policy['calibrator_sha256'] is not None \
            and len(policy['calibration_roots_sha256']) < policy['min_calibration_roots']:
        failed.append('calibration_roots_below_minimum')
    # The pooled count cannot show that the rare classes were calibrated on enough roots.
    if report_version >= 7 and policy['calibrator_sha256'] is not None:
        failed.extend(f'class:{name}:calibration_roots_below_minimum' for name in names
                      if len(policy['calibration_class_roots_sha256'][name]) < policy['min_calibration_roots_per_class'])
    if len(severe_roots) > policy['max_severe_misses']:
        failed.append('severe_misses_above_maximum')
    overflow = max(0, len(abstained)-policy['review_capacity_roots'])
    if overflow:
        failed.append('review_capacity_exceeded')
    try:
        total_cost = sum(errors[k]*costs[k] for k in COSTS)
        if type(total_cost) is float and not math.isfinite(total_cost): total_cost = None
        if total_cost is not None: json.dumps(total_cost,allow_nan=False)
    except (OverflowError,ValueError):
        total_cost = None
    if total_cost is None:
        missing.append('cost_total_unrepresentable')
    result=dict(schema_version=report_version, profile={2:REPORT_PROFILE,3:REPORT_PROFILE_V3,4:REPORT_PROFILE_V4,5:REPORT_PROFILE_V5,6:REPORT_PROFILE_V6,7:REPORT_PROFILE_V7,8:REPORT_PROFILE_V8,9:REPORT_PROFILE_V9}[report_version], role='validate',
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
        threshold_authority='selected_on_declared_tuning_roots_unverified' if policy['threshold_provenance'] == SELECTED
        else 'human_preregistered_unverified', threshold_selection='refused_in_validation',
        root_independence='unverified', label_authority='unverified', qualification='descriptive_only',
        certification='not_issued', automatic_promotion=False, publication_allowed=False)
    if report_version >= 4:
        result['role_separation']='declared_hash_lists_disjoint_only'
    if report_version >= 6:
        result['corpus_sha256'] = policy['corpus_sha256']
    if report_version >= 7:
        result['calibration_class_roots'] = dict(minimum=policy['min_calibration_roots_per_class'],
                                                 counts={name: len(policy['calibration_class_roots_sha256'][name]) for name in names})
    if report_version >= 8:
        result['metric_scopes'] = dict(classes='class_conditional', population_metrics='population_over_labeled_roots')
        result['population_metrics'] = _population_metrics(confusion, classes, decided)
    if report_version >= 5:
        result['score_diagnostics'] = _score_diagnostics(sums, roots, names)
        if result['score_diagnostics']['reliability']['sparse_bins']:
            result['reasons'].insert(len(failed)+len(missing), 'reliability_bins_sparse')
    return result


def select_thresholds(policy, observations, *, min_roots_per_class):
    """Choose each class threshold on declared tuning rows only and freeze them into a policy.

    ``policy`` is a draft: every class threshold unset, ``threshold_provenance`` ``selected_on_tuning_roots`` and
    no tuning roots yet. Its per-class ``min_precision`` and ``min_recall`` are the preregistered targets.
    ``observations`` are rows shaped as for ``evaluate`` but with ``split: tuning``; validation rows are refused.
    A class gets the lowest observed threshold at which its same-sample precision and end-to-end recall meet
    its targets with at least one correct decision. A class with fewer than ``min_roots_per_class`` labeled
    roots, or no such threshold, stays unselected and no policy is issued. These estimates are optimistic by
    construction and grant no authority; the policy still needs an untouched validation (``evaluate`` v9).
    """
    if not _count(min_roots_per_class):
        raise ValueError('the minimum tuning roots per class must be a nonnegative integer')
    names, forbidden = check_policy(policy, require_disjoint_roles=True, require_scope=True,
                                    require_class_calibration=True, selecting=True)
    if policy['model_sha256'] is None:
        raise ValueError('threshold selection requires a policy bound to a model revision')
    classes, margin = policy['classes'], policy['abstention']['min_margin']
    unconstrained = dict.fromkeys(names, dict(threshold=0))  # decisions before any threshold: unique leader, margin met
    roots = {}
    for row in observations:
        digest, label, scores = _read_row(row, policy, names, forbidden, 'tuning', True, True)
        decided, _ = _row_decision(scores, names, unconstrained, margin)
        state = roots.setdefault(digest, [label, dict.fromkeys(names, math.inf)])
        if state[0] != label:
            raise ValueError('conflicting labels within a declared tuning root')
        for name in names:  # -1 marks a class this root can never decide: some row abstained or chose another class
            state[1][name] = min(state[1][name], scores[name]) if name == decided else -1.0
    if not roots:
        raise ValueError('empty triage tuning input')
    summary, chosen, failed = {}, {}, []
    for name in names:
        support = sum(state[0] == name for state in roots.values())
        # Sweep thresholds from high to low; the last satisfying one is the lowest, hence the widest coverage.
        points = sorted(((state[1][name], state[0] == name) for state in roots.values()
                         if state[0] is not None and state[1][name] >= 0), reverse=True)
        best, tp, fp, index = None, 0, 0, 0
        while support >= min_roots_per_class and support and index < len(points):
            value = points[index][0]
            while index < len(points) and points[index][0] == value:
                tp, fp, index = tp+points[index][1], fp+(not points[index][1]), index+1
            if tp and tp/(tp+fp) >= classes[name]['min_precision'] and tp/support >= classes[name]['min_recall']:
                best = (value, tp/(tp+fp), tp/support)
        if best is None:
            failed.append(f'class:{name}:'+('tuning_roots_below_minimum' if support < min_roots_per_class
                                           else 'no_threshold_meets_preregistered_targets'))
        chosen[name] = best[0] if best else None
        summary[name] = dict(severity=classes[name]['severity'], tuning_roots=support, threshold=chosen[name],
                             tuning_precision=best[1] if best else None, tuning_recall=best[2] if best else None,
                             authority='same_sample_descriptive_only')
    frozen = None if failed else deepcopy(policy)
    if frozen:
        frozen['threshold_tuning_roots_sha256'] = sorted(roots)
        for name in names:
            frozen['classes'][name]['threshold'] = chosen[name]
    return dict(schema_version=1, profile=SELECTION_PROFILE, role='select', status='unselected' if failed else 'selected',
                reasons=failed+list(SELECTION_LIMITATIONS), draft_sha256=policy_sha256(policy),
                policy_sha256=policy_sha256(frozen) if frozen else None, model_sha256=policy['model_sha256'],
                corpus_sha256=policy['corpus_sha256'], unit='declared_root', labeled_roots=sum(s[0] is not None for s in roots.values()),
                unlabeled_roots=sum(s[0] is None for s in roots.values()), min_roots_per_class=min_roots_per_class,
                classes=summary, tuning_roots_sha256=sorted(roots), policy=frozen,
                threshold_authority='selected_on_tuning_roots_unverified', certification='not_issued',
                automatic_promotion=False, publication_allowed=False)


def _population_metrics(confusion, classes, decided):
    """Severity-level and alert-level descriptive metrics over labeled roots; nothing here is a gate.

    A metric with no root behind it is None, never 0 or 1.
    """
    grid = {t: dict.fromkeys((*SEVERITIES, ABSTAINED), 0) for t in SEVERITIES}
    for label, outcomes in confusion.items():
        for outcome, count in outcomes.items():
            grid[classes[label]['severity']][ABSTAINED if outcome == ABSTAINED else classes[outcome]['severity']] += count
    by_severity = {}
    for kind in SEVERITIES:
        tp = grid[kind][kind]
        fn = sum(grid[kind][other] for other in SEVERITIES if other != kind)
        fp = sum(grid[other][kind] for other in SEVERITIES if other != kind)
        held = grid[kind][ABSTAINED]
        support = tp+fn+held
        by_severity[kind] = dict(roots=support, tp=tp, fp=fp, fn=fn, tn=decided-tp-fp-fn, abstained=held,
                                 precision=tp/(tp+fp) if tp+fp else None, recall=tp/support if support else None)
    incident = SEVERITIES[:2]  # every severity but 'none' is an incident, and a decision on it an alert
    alerts = sum(grid[t][a] for t in SEVERITIES for a in incident)
    caught = sum(grid[t][a] for t in incident for a in incident)
    incidents = sum(sum(grid[t].values()) for t in incident)
    quiet = sum(grid[t]['none'] for t in SEVERITIES)
    missed = sum(grid[t]['none'] for t in incident)
    alert = dict(incident_roots=incidents, abstained_incident_roots=sum(grid[t][ABSTAINED] for t in incident),
                 alert_roots=alerts, alert_precision=caught/alerts if alerts else None,
                 incident_recall=caught/incidents if incidents else None,
                 accepted_negative_roots=quiet, accepted_negative_error_rate=missed/quiet if quiet else None)
    return dict(unit='declared_root', scope='labeled_roots', by_severity=by_severity, alert=alert, authority='descriptive_only')


def _score_diagnostics(sums, roots, names):
    """Descriptive Brier, top-label reliability and risk/coverage on labeled roots.

    Scores are each root's mean row score; abstaining roots are included because
    these describe the scores, not the frozen decisions. Nothing here is a gate.
    """
    rows = []
    for digest, (n, total) in sums.items():
        label = roots[digest][0]
        if label is None:
            continue
        mean = {name: total[name]/n for name in names}
        top = max(mean.values())
        leaders = [name for name in names if mean[name] == top]
        rows.append((top, leaders == [label], sum((mean[name]-(name == label))**2 for name in names)))
    count = len(rows)
    bins = []
    for index, low in enumerate(BIN_EDGES):
        high = BIN_EDGES[index+1] if index+1 < len(BIN_EDGES) else None
        inside = [r for r in rows if r[0] >= low and (high is None or r[0] < high)]
        bins.append(dict(low=low, high=high if high is not None else 1.0, roots=len(inside),
                         mean_confidence=sum(r[0] for r in inside)/len(inside) if inside else None,
                         accuracy=sum(r[1] for r in inside)/len(inside) if inside else None))
    curve = []
    for low in BIN_EDGES:
        kept = [r for r in rows if r[0] >= low]
        curve.append(dict(min_confidence=low, roots=len(kept), coverage=len(kept)/count if count else None,
                          error_rate=sum(not r[1] for r in kept)/len(kept) if kept else None))
    return dict(unit='declared_root', labeled_roots=count, score_source='mean_row_score_per_root',
                multiclass_brier=sum(r[2] for r in rows)/count if count else None,
                reliability=dict(bins=bins, min_bin_roots=MIN_BIN_ROOTS,
                                 sparse_bins=sum(0 < b['roots'] < MIN_BIN_ROOTS for b in bins)),
                risk_coverage=curve, authority='descriptive_only')
