"""Fail-closed comparability of two evaluations, with explicit strata.

Pure and offline. It decides from facts the caller supplies (frozen plan fields, identities and live authority
results); it reads no store, calls no model and authenticates nothing. Unknown stays unknown: a missing, empty,
non-finite or unserializable fact is reported as unknown and never as a match. Only definite differences in the
stratum gates give `not_comparable`; unknown or invalid evidence gives `insufficient_evidence`."""
import hashlib
import json
import math

SCHEMA = 'ahl-comparability-v1'
# Same value required on both sides; a difference puts the evaluations in separate strata.
STRATUM = ('endpoint', 'error_control', 'task_definitions', 'partition_policy', 'grader_identity', 'task_selection',
           'baseline_identity', 'design', 'runtime', 'model_settings', 'metrics', 'resource_boundaries', 'missingness')
# Must be known on both sides but may differ (the candidate under test, the pinned realized assignment).
KNOWN = ('candidate_identity', 'trial_lineage')
# Live authority and integrity results: only boolean True passes.
CURRENT = ('oracle_authority', 'bank_eligibility', 'evidence_current', 'held_out_integrity', 'fresh_execution')
GATES = STRATUM + KNOWN + CURRENT + ('independent_roots',)
SIDES = ('left', 'right')
MAX_DEPTH = 8
MAX_BYTES = 1 << 20  # canonical JSON per gate; larger is unknown
MAX_ROOT_ID = 128  # the ExperimentSpec identity bound


def _norm(value, depth=0):
    """The value with every container checked, or ValueError when any part of it is unknown."""
    if depth > MAX_DEPTH or value is None or value == '':
        raise ValueError('unknown')
    if isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, (list, tuple)) and value:
        return [_norm(v, depth + 1) for v in value]
    if isinstance(value, dict) and value and all(isinstance(k, str) for k in value):
        return {k: _norm(v, depth + 1) for k, v in value.items()}
    raise ValueError('unknown')


def _text(value):
    """Canonical JSON of a known value, else None."""
    try:  # shortcut: size is checked after serializing, so one oversized fact is still built once
        text = json.dumps(_norm(value), sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError):
        return None
    return text if len(text) <= MAX_BYTES else None


def _roots(facts):
    """Number of distinct declared roots (repetitions of a root count once), or None when unusable."""
    roots, minimum = facts.get('roots'), facts.get('min_roots')
    if not isinstance(roots, (list, tuple)) or not roots or not all(isinstance(r, str) and 0 < len(r) <= MAX_ROOT_ID for r in roots) \
            or type(minimum) is not int or minimum < 2:  # ExperimentSpec refuses fewer than two roots
        return None, None
    return len(set(roots)), minimum


def stratum(facts):
    """Digest of the stratum gates, or None when any of them is unknown."""
    facts = facts if isinstance(facts, dict) else {}
    texts = [_text(facts.get(gate)) for gate in STRATUM]
    if None in texts:
        return None
    return hashlib.sha256(json.dumps(dict(zip(STRATUM, texts)), sort_keys=True).encode()).hexdigest()


def assess(left, right):
    """Decide whether two evaluations' facts may be compared; returns an `ahl-comparability-v1` result.

    Facts map each gate name to its value; roots are given as `roots` (one root id per trial) and `min_roots`.
    Keys outside the gate table (for example an A/A check) are ignored: they diagnose instability and never
    make histories comparable."""
    facts = {'left': left if isinstance(left, dict) else {}, 'right': right if isinstance(right, dict) else {}}
    failures, degraded, counts = [], [], {}
    for gate in GATES:
        if gate == 'independent_roots':
            for side in SIDES:
                count, minimum = _roots(facts[side])
                counts[side] = count
                if count is None:
                    degraded.append({'gate': gate, 'side': side, 'reason': 'unknown'})
                elif count < minimum:
                    degraded.append({'gate': gate, 'side': side, 'reason': 'insufficient'})
        elif gate in CURRENT:
            for side in SIDES:
                value = facts[side].get(gate)
                if value is not True:
                    degraded.append({'gate': gate, 'side': side, 'reason': 'invalid' if value is False else 'unknown'})
        else:
            texts = {side: _text(facts[side].get(gate)) for side in SIDES}
            degraded += [{'gate': gate, 'side': side, 'reason': 'unknown'} for side in SIDES if texts[side] is None]
            if gate in STRATUM and None not in texts.values() and texts['left'] != texts['right']:
                failures.append({'gate': gate, 'reason': 'differs'})
    verdict = 'not_comparable' if failures else 'insufficient_evidence' if degraded else 'comparable'
    return {'schema': SCHEMA, 'verdict': verdict, 'failures': failures, 'evidence_degradation': degraded,
            'stratum': {side: stratum(facts[side]) for side in SIDES},
            'declared_roots': {side: counts[side] for side in SIDES}, 'independence': 'unverified',
            'regression_claim_eligible': verdict == 'comparable',
            'facts_authority': 'caller_supplied_unverified', 'publication_allowed': False}


def group_strata(evaluations):
    """Group evaluation ids by stratum; an evaluation with an unknown stratum gate is unassigned, never merged."""
    strata, unassigned = {}, []
    for name in sorted(evaluations):
        key = stratum(evaluations[name])
        (unassigned if key is None else strata.setdefault(key, [])).append(name)
    return {'strata': strata, 'unassigned': unassigned}
