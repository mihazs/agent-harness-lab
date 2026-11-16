"""A candidate that regresses a scored area stops the loop as a regression, and an unmeasured area never does (#798)."""
from dataclasses import replace

import pytest

from agent_harness_lab.loop import controller
from agent_harness_lab.loop.spec import resolve
from tests.support import loop_sim
from tests.support.loop_sim import build, score

pytestmark = pytest.mark.usefixtures('sealed')


def spec(**declaration):
    return resolve(limits=dict(seconds=600, trials=100), project=dict(max_rounds=3, plateau_rounds=2, **declaration))


def drive(script, tmp_path):
    result, ledger, ports = loop_sim.drive(script, tmp_path)
    assert ledger.replay(script.spec).stop == result['stop']
    return result, ledger, ports


def pooled_win(**fields):
    """The baseline, then a candidate that clears the 0.8 bar overall."""
    return build('pooled_win', [score(0.6), score(0.9, **fields)], declaration=dict(regression_margin=0.05))


@pytest.mark.parametrize('cause', [pytest.param('candidate', id='candidate-caused'), pytest.param(None, id='unknown-cause')])
@pytest.mark.parametrize('policy', ['stop', 'rollback_continue'])
def test_a_regressed_area_stops_the_loop_though_the_pooled_score_meets_the_bar(tmp_path, policy, cause):
    script = pooled_win(regressed_areas=('ux-ui',), cause=cause)
    script = replace(script, declaration={**script.declaration, 'on_regression': policy})
    result, ledger, ports = drive(script, tmp_path)
    assert (result['outcome'], result['stop'], result['best']['snapshot']) == ('regressed', 'regression', ports.baseline)
    assert [payload['regression'] for kind, payload in ledger.events() if kind == 'decided'] == ['area']


def test_the_same_candidate_with_no_regressed_area_meets_the_bar(tmp_path):
    result, _, ports = drive(pooled_win(), tmp_path)
    assert (result['outcome'], result['stop'], result['best']['snapshot']) == ('accepted', 'acceptance_score_reached', ports.evaluations.calls[-1])


def test_the_regressed_areas_are_kept_in_the_ledger_summary_and_the_receipt_round(tmp_path):
    from agent_harness_lab.loop import receipt
    script = pooled_win(regressed_areas=('ux-ui',))
    result, ledger, _ = drive(script, tmp_path)
    block = receipt.build(script.spec, result, ledger.events(), [None, None])
    assert [round_['regressed_areas'] for round_ in block['rounds']] == [['ux-ui']] and 'regressed_areas' not in block['before']


def test_an_evaluation_without_regressed_areas_adds_no_key_to_the_receipt(tmp_path):
    from agent_harness_lab.loop import receipt
    script = pooled_win()
    result, ledger, _ = drive(script, tmp_path)
    assert all('regressed_areas' not in round_ for round_ in receipt.build(script.spec, result, ledger.events(), [None, None])['rounds'])


@pytest.mark.parametrize('found, kind', [
    pytest.param(score(0.9, critical=True, regressed_areas=('ux-ui',)), 'critical', id='a_critical_flag_comes_first'),
    pytest.param(score(0.9, regressed_areas=('ux-ui',)), 'area', id='a_regressed_area'),
    pytest.param(score(0.9, areas=(('a', 0.2),), regressed_areas=()), 'none', id='no_regressed_area_and_no_baseline_area_to_fall_from'),
])
def test_the_regression_kind_names_the_critical_flag_before_a_regressed_area(found, kind):
    assert controller.regression(found, score(0.6), spec()) == kind
