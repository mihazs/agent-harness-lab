import copy

import pytest

from agent_harness_lab.evaluation.resource_decision import decide

TIME = dict(name='elapsed', kind='elapsed_wall_seconds', unit='s', scope='all_assigned', required=True, horizon=100, max_increase=5)
TOKENS = dict(name='tokens', kind='measured_tokens', unit='tokens', scope='all_assigned', required=False, horizon=1000, max_increase=50)


def contract(*endpoints, **over):
    return dict(schema_version=1, profile='resource-noninferiority-v1', alpha=0.05, min_roots=10,
                endpoints=[dict(e) for e in (endpoints or (TIME,))], **over)


def arm(elapsed, status='completed', **extra):
    return dict(status=status, measurements=dict(elapsed=elapsed, **extra))


def pairs(baseline, candidate, roots=100, **arms):
    return [dict(root=f'r{i}', baseline=arm(baseline, **arms.get('b', {})), candidate=arm(candidate, **arms.get('c', {})))
            for i in range(roots)]


def test_quality_preserving_gain_is_an_improvement():
    result = decide(contract(), pairs(60, 10), 'supported')
    assert (result['decision'], result['efficiency_improvement']) == ('improvement', True)
    assert result['endpoints'][0]['high'] < 0 and result['automatic_promotion'] is False


@pytest.mark.parametrize('quality', [None, 'inconclusive', 'rejected', 'incomplete', 'blocked'])
def test_fast_failure_is_never_an_efficiency_improvement(quality):
    result = decide(contract(), pairs(60, 1), quality)
    assert result['decision'] == 'quality_not_verified' and result['efficiency_improvement'] is False
    assert result['endpoints'][0]['status'] == 'improvement'  # still shown for human tradeoff review


def test_true_regression_and_wide_evidence_are_distinct():
    assert decide(contract(), pairs(10, 90), 'supported')['decision'] == 'regression'
    assert decide(contract(), pairs(50, 50), 'supported')['decision'] == 'inconclusive'
    assert decide(contract(), pairs(50, 50, roots=3), 'supported')['endpoints'][0]['reason'] == 'insufficient_independent_roots'


def test_noninferior_is_not_a_claimed_improvement():
    loose = contract(dict(TIME, max_increase=30))
    result = decide(loose, pairs(50, 50), 'supported')
    assert (result['decision'], result['efficiency_improvement']) == ('noninferior', False)


def test_slow_timeouts_stay_in_the_all_assigned_estimate():
    rows = pairs(40, 10)
    for row in rows: row['candidate'] = arm(300, 'agent_timeout')  # fast successes are not the whole story
    result = decide(contract(), rows, 'supported')
    assert result['decision'] == 'regression' and result['endpoints'][0]['capped_observations'] == 100


@pytest.mark.parametrize('trial', [arm(None), arm(-1), arm(True), arm(20, 'agent_timeout'), arm(20, 'infrastructure_interruption'), arm(20, 'cancelled')])
def test_unknown_required_measurement_blocks_a_favorable_decision(trial):
    rows = pairs(60, 10)
    rows[0]['candidate'] = trial
    result = decide(contract(), rows, 'supported')
    assert result['decision'] == 'blocked' and result['efficiency_improvement'] is False
    assert result['endpoints'][0]['status'] == 'unknown'


def test_no_pairs_is_unknown_not_zero():
    assert decide(contract(), [], 'supported')['decision'] == 'blocked'


def test_advisory_endpoint_absent_stays_unverified_without_changing_the_decision():
    result = decide(contract(TIME, TOKENS), pairs(60, 10), 'supported')
    tokens = result['endpoints'][1]
    assert (result['decision'], tokens['status'], tokens['unknown_reasons']) == ('improvement', 'unknown', {'missing_measurement': 200})


def test_required_tokens_missing_blocks():
    both = contract(TIME, dict(TOKENS, required=True))
    assert decide(both, pairs(60, 10), 'supported')['decision'] == 'blocked'


def test_contract_change_creates_a_new_identity():
    base = decide(contract(), pairs(60, 10), 'supported')
    changed = decide(contract(dict(TIME, max_increase=6)), pairs(60, 10), 'supported')
    assert base['contract_sha256'] != changed['contract_sha256']
    assert base['contract_sha256'] == decide(contract(), pairs(60, 10), 'supported')['contract_sha256']


@pytest.mark.parametrize('edit', [
    lambda c: c['endpoints'][0].update(kind='currency'),
    lambda c: c['endpoints'][0].update(scope='success_conditional'),
    lambda c: c['endpoints'][0].update(horizon=0),
    lambda c: c['endpoints'][0].update(required=False),
    lambda c: c.update(alpha=1.5),
    lambda c: c.update(extra=1),
])
def test_invalid_or_unsupported_contract_is_refused(edit):
    bad = copy.deepcopy(contract())
    edit(bad)
    with pytest.raises(ValueError):
        decide(bad, pairs(60, 10), 'supported')
