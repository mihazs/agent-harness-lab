"""Recorded-response controls for a pairwise diagnostic judge on held-out roots (#14); validation role only.

This module never calls a model: it checks responses someone recorded earlier. Every root is judged in
both presentation orders, so a verdict that follows the arm survives the swap and one that follows the
position does not. Matched control roots whose arms differ only in style, verbosity or wording must
resolve to a tie. Cited evidence is checked against the case's own spans, an executable violation can
never be cleared by the judge, and self-reported confidence is never read. A frozen human policy supplies
every limit; the best status is ``insufficient_evidence`` and no diagnosis authority is ever granted.
"""
import hashlib
import json

POLICY_PROFILE = 'diagnostic-judge-policy-v1'
REPORT_PROFILE = 'diagnostic-judge-evaluation-v1'
MAXIMA = ('max_order_disagreement_rate', 'max_control_preference_rate', 'max_citation_failure_rate')
POLICY_FIELDS = frozenset(('schema_version', 'profile', 'judge_ref', 'rubric_revision', 'min_coverage',
                           'min_label_agreement') + MAXIMA)
ORDERS = {'first_second': ('first', 'second'), 'second_first': ('second', 'first')}
LABELS = ('first', 'second', 'tie')
POSITIONS = ('A', 'B', 'tie')
CONTROL_FACTORS = ('style', 'verbosity', 'wording')
STATES = ('decided', 'abstained', 'order_disagreement', 'order_unchecked', 'invalid_verdict')
CITATIONS = ('supported', 'unsupported', 'fabricated', 'uncited', 'existence_only', 'unverifiable')
_CITATION_FAILURES = ('unsupported', 'fabricated', 'uncited')
_ID_CHARS, _SPANS, _CITED = 128, 1024, 64
LIMITATIONS = ('blinding_unverified', 'label_authority_unverified', 'root_independence_unverified',
               'policy_preregistration_unverified')


def _rate(value):
    return type(value) in (int, float) and 0 <= value <= 1  # false for nan, inf and ints beyond float range


def _ident(value):
    return isinstance(value, str) and 0 < len(value) <= _ID_CHARS


def _ids(value, limit):
    return isinstance(value, list) and len(value) <= limit and all(_ident(v) for v in value)


def check_policy(policy):
    if (not isinstance(policy, dict) or set(policy) != POLICY_FIELDS or type(policy['schema_version']) is not int
            or policy['schema_version'] != 1 or policy['profile'] != POLICY_PROFILE):
        raise ValueError('invalid judge policy schema')
    if not (_ident(policy['judge_ref']) and _ident(policy['rubric_revision'])):
        raise ValueError('judge policy requires a frozen judge_ref and rubric_revision')
    if not (all(_rate(policy[k]) and policy[k] > 0 for k in ('min_coverage', 'min_label_agreement'))
            and all(_rate(policy[k]) for k in MAXIMA)):
        raise ValueError('judge limits must be rates in [0, 1] and minimums above 0')


def _responses(row, policy):
    """The recorded responses keyed by presentation order; a different judge or rubric is never mixed in."""
    items = row.get('responses')
    if not isinstance(items, list) or not 1 <= len(items) <= len(ORDERS):
        raise ValueError('judge validation requires one or two recorded responses per root')
    by_order = {}
    for item in items:
        if (not isinstance(item, dict) or item.get('judge_ref') != policy['judge_ref']
                or item.get('rubric_revision') != policy['rubric_revision']):
            raise ValueError('response identity differs from the frozen judge protocol')
        order = item.get('presented')
        if not isinstance(order, str) or order not in ORDERS or order in by_order:
            raise ValueError('response presentation order is missing, unknown or repeated')
        for key, meaning in (('verdict', 'abstention'), ('citations', 'an uncited claim')):
            if key not in item:  # an omitted key is unknown capture, never an abstention or an uncited claim
                raise ValueError(f'a recorded response must state its {key}; omission is not {meaning}')
        if not _ids(item['citations'], _CITED):
            raise ValueError('invalid response citations')
        if item.get('confidence') is not None and not _rate(item['confidence']):
            raise ValueError('invalid self-reported confidence')
        by_order[order] = item
    return by_order


def _decision(by_order):
    """(state, arm) for one root; only a verdict that survives the order swap is decided."""
    verdicts = {order: item['verdict'] for order, item in by_order.items()}
    if len(verdicts) < len(ORDERS):
        return 'order_unchecked', None
    if any(v is not None and v not in POSITIONS for v in verdicts.values()):
        return 'invalid_verdict', None
    if None in verdicts.values():
        return 'abstained', None
    arms = {'tie' if v == 'tie' else ORDERS[order][v == 'B'] for order, v in verdicts.items()}
    return ('decided', arms.pop()) if len(arms) == 1 else ('order_disagreement', None)


def _spans(row):
    spans, support = row.get('spans'), row.get('supporting_spans')
    if (spans is not None and not _ids(spans, _SPANS)) or (support is not None and not (
            spans is not None and _ids(support, _SPANS) and set(support) <= set(spans))):
        raise ValueError('case spans must be identifiers and reviewed support a subset of them')
    return spans, support


def _citation(cited, arm, label, spans, support):
    """Existence of a citation is not support for the claim; unreviewed support stays unknown."""
    if not cited:
        return 'uncited'
    if spans is None:
        return 'unverifiable'
    if not set(cited) <= set(spans):
        return 'fabricated'
    if support is None or label is None:
        return 'existence_only'
    return 'supported' if arm == label and set(cited) <= set(support) else 'unsupported'


def _share(part, whole):
    return part / whole if whole else None


def evaluate(policy, observations):
    """Describe the recorded judge on held-out roots; supplied labels and citations grant no authority.

    A row has ``root_case_id``, ``split: heldout``, ``label`` (``first``, ``second``, ``tie`` or None for
    unknown), ``responses`` (one per presentation order: ``judge_ref``, ``rubric_revision``, ``presented``,
    ``verdict`` of ``A``, ``B``, ``tie`` or None for abstention, ``citations`` (``[]`` when it cited
    nothing; both keys are required, since omission is unknown capture) and an optional unused
    ``confidence``) and optionally ``spans`` (the case's evidence identifiers), ``supporting_spans``
    (reviewed as supporting the label) and ``violating_arm`` (an arm an executable check flagged). A row
    with ``control`` set to a factor from ``CONTROL_FACTORS`` is a matched control whose label must be
    ``tie``. One row per root.
    """
    check_policy(policy)
    seen, controls = set(), dict(rows=0, preferences=0, states=dict.fromkeys(STATES, 0))
    factors = {name: dict(controls=0, preferences=0) for name in CONTROL_FACTORS}
    cases, cited = dict.fromkeys(STATES, 0), dict.fromkeys(CITATIONS, 0)
    correct = wrong = unknown = overrides = picks_a = picks_b = 0
    for row in observations:
        if not isinstance(row, dict) or row.get('split') != 'heldout':
            raise ValueError('judge validation requires heldout rows')
        root = row.get('root_case_id')
        if not isinstance(root, str) or not root or len(root) > 256 or root in seen:
            raise ValueError('judge validation requires one row per declared root')
        seen.add(root)
        label, factor = row.get('label'), row.get('control')
        if label is not None and label not in LABELS:
            raise ValueError('label outside first, second, tie or unknown')
        if factor is not None and (factor not in CONTROL_FACTORS or label != 'tie'):
            raise ValueError('a control needs a known factor and the tie label')
        if row.get('violating_arm') not in (None, 'first', 'second'):
            raise ValueError('violating_arm must be first, second or absent')
        by_order = _responses(row, policy)
        spans, support = _spans(row)
        state, arm = _decision(by_order)
        for item in by_order.values():
            picks_a += item['verdict'] == 'A'
            picks_b += item['verdict'] == 'B'
        if factor is not None:
            controls['rows'] += 1
            controls['states'][state] += 1
            factors[factor]['controls'] += 1
            preferred = arm in ('first', 'second')
            controls['preferences'] += preferred
            factors[factor]['preferences'] += preferred
            continue
        cases[state] += 1
        unknown += label is None
        if state != 'decided':
            continue
        violating = row.get('violating_arm')
        overrides += violating is not None and arm != ('second' if violating == 'first' else 'first')
        if label is not None:
            correct += arm == label
            wrong += arm != label
        if arm != 'tie':
            union = sorted({c for item in by_order.values() for c in item['citations']})
            cited[_citation(union, arm, label, spans, support)] += 1
    roots = sum(cases.values())
    if not roots:
        raise ValueError('judge validation requires at least one case root')
    coverage = cases['decided'] / roots
    agreement = _share(correct, correct + wrong)
    both = cases['decided'] + cases['order_disagreement']
    disagreement = _share(cases['order_disagreement'], both)
    control_rate = _share(controls['preferences'], controls['rows'])
    verifiable = sum(cited[k] for k in _CITATION_FAILURES) + cited['supported']
    citation_rate = _share(sum(cited[k] for k in _CITATION_FAILURES), verifiable)
    failed = []
    if coverage < policy['min_coverage']:
        failed.append('coverage_below_minimum')
    for value, key, name, below in ((agreement, 'min_label_agreement', 'label_agreement_below_minimum', True),
                                    (disagreement, 'max_order_disagreement_rate', 'order_disagreement_above_maximum', False),
                                    (control_rate, 'max_control_preference_rate', 'control_preference_above_maximum', False),
                                    (citation_rate, 'max_citation_failure_rate', 'citation_failures_above_maximum', False)):
        if value is not None and (value < policy[key] if below else value > policy[key]):
            failed.append(name)
    if overrides:
        failed.append('executable_violation_overridden')
    open_controls = controls['rows'] - controls['states']['decided']
    missing = [name for name, hit in (
        ('zero_coverage', not cases['decided']), ('no_labeled_decisions', not (correct + wrong)),
        ('orders_unchecked', cases['order_unchecked'] + controls['states']['order_unchecked'] > 0),
        ('invalid_verdicts_present', cases['invalid_verdict'] + controls['states']['invalid_verdict'] > 0),
        ('no_controls', not controls['rows']), ('controls_unresolved', open_controls > 0),
        ('citations_unverified', cited['existence_only'] + cited['unverifiable'] > 0)) if hit]
    return dict(
        schema_version=1, profile=REPORT_PROFILE, role='validate',
        status='rejected' if failed else 'insufficient_evidence', reasons=failed + missing + list(LIMITATIONS),
        policy_sha256=hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        judge_ref=policy['judge_ref'], rubric_revision=policy['rubric_revision'],
        unit='declared_root', roots=roots, states=cases, coverage=coverage, unknown_labels=unknown,
        label_agreement=dict(correct=correct, wrong=wrong, rate=agreement),
        order_disagreement_rate=disagreement, position_picks=dict(A=picks_a, B=picks_b),
        controls=dict(rows=controls['rows'], preferences=controls['preferences'], rate=control_rate,
                      states=controls['states'], by_factor=factors),
        citations=dict(cited, failure_rate=citation_rate),
        executable_violations_overridden=overrides, self_reported_confidence='ignored_uncalibrated',
        authoritative_diagnosis=False, beneficial_claim='not_issued', qualification='descriptive_only',
        certification='not_issued', automatic_promotion=False, promotion_authority=False,
        publication_allowed=False, blinding='unverified', label_authority='unverified',
        root_independence='unverified')
