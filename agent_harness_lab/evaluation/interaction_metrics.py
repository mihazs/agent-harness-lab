"""Separate action-specific counts over graded interaction episodes (#123).

Each trial is a caller-supplied (contract, evidence) pair graded by `interaction_contract.evaluate`.
Counts are never merged into one score, no rate is computed, and an unknown stays unknown: an
unobserved channel adds to `unknowns`, never to a violation, a failure or a success.
"""
from collections import Counter

from .interaction_contract import evaluate

PROFILE = 'interaction-metrics-v1'
MAX_TRIALS = 5000


def _tally(values):
    counts = Counter('unknown' if value is None else str(value).lower() for value in values)
    return {key: counts[key] for key in ('true', 'false', 'unknown')}


def summarize(trials):
    """Return counts over a bounded list of (contract, evidence) pairs; raise ValueError on a bad batch."""
    if (not isinstance(trials, list) or len(trials) > MAX_TRIALS
            or any(not isinstance(item, (tuple, list)) or len(item) != 2 for item in trials)):
        raise ValueError('bounded list of (contract, evidence) interaction trials required')
    graded = [(contract, evaluate(contract, evidence)) for contract, evidence in trials]
    valid = [(contract, result) for contract, result in graded if result['contract_sha256'] is not None]
    protected = [result['state_preserved'] for contract, result in valid if contract['protected_paths']]
    stops = [result['interaction_requirement_satisfied'] for contract, result in valid if contract['stop_reason'] is not None]
    state = _tally(protected + [None] * (len(graded) - len(valid)))
    state['not_applicable'] = len(valid) - len(protected)
    return dict(
        profile=PROFILE, trials=len(graded), contracts=len({result['contract_sha256'] for _, result in valid}),
        passed=_tally(result['passed'] for _, result in graded),
        interaction_requirement_satisfied=_tally(result['interaction_requirement_satisfied'] for _, result in graded),
        whole_task_completed=_tally(result['whole_task_completed'] for _, result in graded),
        state_preserved=state,
        # `correct` needs the full interaction requirement, so one other violation keeps a good stop uncounted.
        deliberate_stop=dict(declared=len(stops), correct=stops.count(True)),
        violations=dict(sorted(Counter(name for _, result in graded for name in result['violations']).items())),
        unknowns=dict(sorted(Counter(name for _, result in graded for name in result['unknowns']).items())),
        publication_allowed=False, automatic_promotion=False)
