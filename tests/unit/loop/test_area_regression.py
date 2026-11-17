"""A candidate that regresses a scored area stops the loop as a regression, and an unmeasured area never does (#798)."""
from dataclasses import replace

import pytest

from agent_harness_lab import exit_guidance, typed_exit
from agent_harness_lab.loop import controller, receipt
from agent_harness_lab.loop.spec import resolve
from tests.support import loop_sim
from tests.support.loop_claims import claims
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
    script = pooled_win(regressed_areas=('ux-ui',))
    result, ledger, _ = drive(script, tmp_path)
    block = receipt.build(script.spec, result, ledger.events(), [None, None])
    assert [round_['regressed_areas'] for round_ in block['rounds']] == [['ux-ui']] and 'regressed_areas' not in block['before']


def test_an_evaluation_without_regressed_areas_adds_no_key_to_the_receipt(tmp_path):
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


def stopped(script, tmp_path):
    """The receipt block of a loop that ran `script` to its stop criterion."""
    result, ledger, _ = drive(script, tmp_path)
    return receipt.build(script.spec, result, ledger.events(), [None, None])


def test_the_remediation_of_a_regression_stop_names_the_area_evidence_when_a_regressed_area_stopped_the_loop(tmp_path):
    block = stopped(pooled_win(regressed_areas=('ux-ui',)), tmp_path)
    step = block['remediation']
    assert block['stop_criterion'] == 'regression' and step != receipt.STEPS['regression']
    assert 'regressed_areas' in step and 'area_gates' in step and step.endswith('.') and not claims(step)


def test_a_pooled_regression_without_a_regressed_area_keeps_the_generic_remediation(tmp_path):
    block = stopped(build('pooled_loss', [score(0.6), score(0.3)], declaration=dict(regression_margin=0.05)), tmp_path)
    assert (block['stop_criterion'], block['remediation']) == ('regression', receipt.STEPS['regression'])


def test_the_summary_and_the_exit_receipt_of_a_loop_stop_read_the_remediation_the_loop_recorded():
    step = 'The one step this loop block recorded for its own stop.'
    result = dict(status='not_accepted', reason='loop_regression', loop=dict(remediation=step))
    assert exit_guidance.guidance(result)[2] == step
    assert typed_exit.seal(dict(result))['exit_receipt']['next_step'] == step


@pytest.mark.parametrize('loop', [
    pytest.param(None, id='no_loop_block'), pytest.param({}, id='no_remediation'), pytest.param(dict(remediation=''), id='empty_remediation'),
    pytest.param(dict(remediation=7), id='remediation_not_text'), pytest.param('text', id='loop_block_not_a_mapping'),
])
def test_a_loop_stop_whose_block_records_no_usable_remediation_keeps_the_generic_step(loop):
    result = dict(status='not_accepted', reason='loop_regression', loop=loop)
    assert exit_guidance.guidance(result)[2] == receipt.STEPS['regression']
    assert typed_exit.seal(dict(result))['exit_receipt']['next_step'] == receipt.STEPS['regression']
