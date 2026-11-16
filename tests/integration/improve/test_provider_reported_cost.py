"""Provider-reported cost (#823): a run that spent preparation shows the runtime's own figures, labelled unverified, never as AHL's.

Every cell builds a synthetic project and recorded sessions under tmp_path with pinned dates and a scripted fake runtime.
"""
import json
from contextlib import ExitStack

import pytest

from tests.integration.workflow.test_budget_stages_end_to_end import Budgeted
from tests.support import loop_improve as li
from tests.support.first_use_history import Home, improve

FIGURES = dict(duration_ms=4200, num_turns=3, total_cost_usd=0.0123, usage=dict(input_tokens=11, output_tokens=7))
LINE = 'ahl: provider-reported cost: preparation-0=0.0123 usd (unverified)'


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = Home(tmp_path, monkeypatch)
    for name in ('a', 'b', 'c'): home.codex(name)
    return home


def outcome_line(err):
    return next(line for line in err.splitlines() if line.startswith('ahl: outcome='))


def test_a_claude_preparation_that_reports_its_figures_carries_them_in_the_receipt_and_the_terminal(home):
    home.closes_with(**FIGURES)
    code, receipt, err = improve(home, '--focus', 'synthetic hypothesis', agent='claude')
    (call,) = receipt['model_calls']
    assert (call['stage'], call['attempt']) == ('preparation', 0)
    assert call['provider_reported'] == dict(profile='ahl-provider-reported-v1', source='provider_reported_unverified', controller_verified=False,
                                             certification_authority=False, duration_ms=4200, num_turns=3, reported_cost=0.0123,
                                             reported_cost_unit='usd', usage=dict(input_tokens=11, output_tokens=7))
    assert receipt['spend_label'] == 'spent_without_evaluation' and receipt['status'] == 'no_change'
    assert err.splitlines().count(LINE) == 1 and err.index(LINE) < err.index('ahl: outcome=')
    assert 'trials_spent=unknown cost=unknown' in outcome_line(err)  # AHL itself still measures no money


def test_a_claude_preparation_without_a_cost_has_the_other_figures_and_no_terminal_cost_line(home):
    home.closes_with(duration_ms=900, num_turns=1)
    code, receipt, err = improve(home, '--focus', 'synthetic hypothesis', agent='claude')
    block = receipt['model_calls'][0]['provider_reported']
    assert (block['duration_ms'], block['num_turns']) == (900, 1)
    assert not {'reported_cost', 'reported_cost_unit', 'usage'} & block.keys()
    assert 'provider-reported cost' not in err and 'cost=unknown' in outcome_line(err)


def test_a_claude_preparation_with_no_figures_has_a_call_entry_and_no_block_rather_than_zeros(home):
    home.closes_with()
    code, receipt, err = improve(home, '--focus', 'synthetic hypothesis', agent='claude')
    assert receipt['model_calls'] == [dict(stage='preparation', attempt=0)]
    assert 'provider-reported' not in err and '"reported_cost": 0' not in json.dumps(receipt)


def test_a_malformed_cost_never_becomes_a_number_in_the_receipt_or_the_terminal(home):
    home.closes_with(total_cost_usd='0.0123 dollars', duration_ms=-1, num_turns='3', usage='lots')
    code, receipt, err = improve(home, '--focus', 'synthetic hypothesis', agent='claude')
    assert receipt['model_calls'] == [dict(stage='preparation', attempt=0)]
    assert 'provider-reported' not in err


def test_a_codex_preparation_reports_token_usage_only_and_no_cost_line(home):
    code, receipt, err = improve(home, '--focus', 'synthetic hypothesis')
    (call,) = receipt['model_calls']
    assert call['provider_reported']['usage'] == dict(output_tokens=1)
    assert not {'reported_cost', 'duration_ms', 'num_turns'} & call['provider_reported'].keys()
    assert 'provider-reported cost' not in err and 'cost=unknown' in outcome_line(err)


def test_a_run_that_made_no_model_call_has_no_model_calls_and_no_cost_line(home):
    code, receipt, err = improve(home)
    assert home.model_calls == 0 and receipt['reason'] == 'no_outcome_evidence'
    assert 'model_calls' not in receipt and 'provider-reported' not in err


def test_the_figures_never_change_the_decision_or_the_label(home):
    home.closes_with(**FIGURES)
    _, with_figures, _ = improve(home, '--focus', 'synthetic hypothesis', agent='claude')
    home.closes_with()
    _, without_figures, _ = improve(home, '--focus', 'synthetic hypothesis', agent='claude')
    keep = ('status', 'reason', 'spend_label', 'changed_files', 'automatic_promotion')  # not preparation_seconds: wall time differs per run
    assert {name: with_figures.get(name) for name in keep} == {name: without_figures.get(name) for name in keep}
    for found in (with_figures, without_figures):
        assert found['spent_without_evaluation']['provider_reported_spend'] == 'unverified'


def test_a_loop_run_records_each_preparation_call_in_the_receipt(monkeypatch):
    from agent_harness_lab.loop import improve as loop_improve
    monkeypatch.setattr(loop_improve, 'mechanisms', lambda args: ['m1', 'm2', 'm3'])
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, script={0: dict(harness=li.EVERYTHING)}, loop=dict(alpha=0.999))
        _, found, _ = project.invoke('--accept-score', '0.1')
    (call,) = found['model_calls']
    assert (call['stage'], call['attempt']) == ('preparation', 0)
    assert call['provider_reported']['usage'] == dict(output_tokens=1)
    assert call['provider_reported']['source'] == 'provider_reported_unverified'


@pytest.mark.parametrize('runtime', ['codex', 'claude'])
def test_a_run_that_constructs_an_evaluation_lists_the_construction_call_after_preparation(tmp_path, monkeypatch, capsys, runtime):
    code, receipt, run, rows = Budgeted(tmp_path, monkeypatch, runtime).run(capsys, {})
    stages = [(call['stage'], call['attempt']) for call in receipt['model_calls']]
    assert stages[:2] == [('preparation', 0), ('construction', 0)], stages
    assert all(call['provider_reported']['usage'] == dict(output_tokens=1) for call in receipt['model_calls'])  # what the stub's records carry
    assert not any('reported_cost' in call['provider_reported'] for call in receipt['model_calls'])
