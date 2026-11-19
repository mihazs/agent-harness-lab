"""A second `ahl improve` reads the first and reports the delta (#806), on synthetic two-run sequences only.

The same fake agent plays proposer, construction and trials. Run one leaves a candidate (`good\\n`) or a regressed one
(`worse`); the project's harness file is then applied, left alone or edited by hand before run two. Nothing reads a real
project, history or model.
"""
import json
from contextlib import ExitStack
from pathlib import Path

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.workflow import improve
from tests.integration.workflow import test_before_after_guard as guard_fixture
from tests.integration.workflow import test_workflow as workflow_fixture


@pytest.fixture
def runs():
    """Run the real workflow entry repeatedly on one project and one private run store; a harness edit is `base.project`."""
    with ExitStack() as cleanups:
        base = workflow_fixture.WorkflowTests(cleanups.callback)
        base.agent.write_text(guard_fixture.WORSE)

        def go(mode='ok', *, harness=None):
            if harness is not None: (base.project / 'AGENTS.md').write_text(harness)
            (base.project / 'mode').write_text(mode)
            result = improve(base.args())
            return result, Path(result['output'])
        go.base = base
        yield go


def edit(runs, text):
    (runs.base.project / 'AGENTS.md').write_text(text)


def test_a_first_run_carries_no_incremental_block(runs):
    result, output = runs('ok', harness='bad')
    assert 'incremental' not in result
    record = json.loads((output / 'harness-states.json').read_text())
    assert sorted(record['baseline']) == ['AGENTS.md'] and sorted(record['candidate']['states']) == ['AGENTS.md']
    assert record['candidate']['attempt'] == 0 and record['scope'] is None


def test_an_applied_candidate_is_reported_with_its_score_as_a_historical_observation(runs):
    first, first_output = runs('ok', harness='bad')
    assert first['status'] == 'inconclusive'
    edit(runs, 'good\n')  # the candidate's exact bytes, applied by hand
    second, output = runs('worse')
    block = second['incremental']
    assert block['disposition'] == 'applied' and block['status'] == 'compared' and block['flags'] == []
    assert block['previous'] == dict(run=first_output.name, status='inconclusive', reason=first['reason'], candidate_frozen=True, candidate_regressed=False)
    assert block['harness']['applied'] == dict(count=1, paths=['AGENTS.md'])
    assert block['harness']['ignored']['count'] == 0 and block['harness']['edited']['count'] == 0
    assert block['harness']['changed_since_previous_baseline'] is True
    historical, fresh = block['historical'], block['fresh']
    assert historical['label'] == 'historical_observation' and fresh['label'] == 'fresh_evaluation'
    assert (historical['baseline_score'], historical['candidate_score']) == pytest.approx((1 / 3, 1))
    assert fresh['baseline_score'] == 1  # the applied candidate is now the fresh baseline
    assert fresh['comparison']['status'] == 'identical'
    assert fresh['fresh_baseline_minus_historical_candidate'] == 0 and fresh['fresh_baseline_minus_historical_baseline'] == pytest.approx(2 / 3)
    assert block['adoption_authority'] is block['automatic_promotion'] is block['publication_allowed'] is False
    assert json.loads((output / 'workflow.json').read_text())['incremental'] == block


def test_an_ignored_candidate_is_reported_as_still_pending_and_nothing_else(runs):
    first, first_output = runs('ok', harness='bad')
    second, _ = runs('ok')
    block = second['incremental']
    assert block['disposition'] == 'ignored' and block['previous']['run'] == first_output.name
    assert block['harness']['ignored'] == dict(count=1, paths=['AGENTS.md']) and block['harness']['applied']['count'] == 0
    assert block['harness']['changed_since_previous_baseline'] is False
    assert block['harness']['added']['count'] == block['harness']['removed']['count'] == 0
    assert block['fresh']['fresh_baseline_minus_historical_baseline'] == 0  # identical baseline on identical frozen inputs
    assert second['status'] == first['status'] == 'inconclusive'  # the earlier trials are not reused: a fresh evaluation ran
    assert (Path(second['output']) / 'evaluation-0/results.json').is_file()


def test_a_regressed_candidate_is_not_evaluated_again_and_the_delta_names_the_earlier_run(runs):
    first, first_output = runs('worse', harness='good')
    assert (first['status'], first['reason']) == ('blocked', 'candidate_regressed')
    second, output = runs('worse')
    assert (second['status'], second['reason'], second['previous_run']) == ('blocked', 'candidate_already_regressed', first_output.name)
    assert second['exit_receipt']['stage'] == 'preparation' and second['exit_receipt']['code'] == 3
    block = second['incremental']
    assert block['disposition'] == 'ignored' and block['previous']['candidate_regressed'] is True
    assert block['stages']['evaluation_construction']['action'] == block['stages']['trials']['action'] == 'skipped'
    assert block['stages']['candidate_preparation']['action'] == 'rerun' and block['fresh'] is None
    assert not (output / 'evaluation-0').exists() and not (output / 'preparation/evaluation-0').exists()
    assert 'before_after' not in second and 'investigation' not in second
    assert block['historical']['verdict'] == 'candidate_regressed' and block['historical']['delta'] < 0
    assert (runs.base.project / 'AGENTS.md').read_text() == 'good'


def test_a_regressed_candidate_is_not_evaluated_again_by_the_third_run_or_any_later_one(runs):
    first, first_output = runs('worse', harness='good')
    assert (first['status'], first['reason']) == ('blocked', 'candidate_regressed')
    previous = first_output
    for _ in range(3):
        repeat, output = runs('worse')
        assert (repeat['status'], repeat['reason'], repeat['previous_run']) == ('blocked', 'candidate_already_regressed', first_output.name)
        assert not (output / 'evaluation-0').exists() and 'before_after' not in repeat
        block = repeat['incremental']
        assert block['previous']['run'] == previous.name and block['previous']['candidate_regressed'] is True
        assert block['historical']['verdict'] == 'candidate_regressed'
        previous = output
    assert (runs.base.project / 'AGENTS.md').read_text() == 'good'


def test_a_run_that_proposed_nothing_does_not_make_the_next_run_forget_a_regression(runs):
    first, first_output = runs('worse', harness='good')
    quiet, _ = runs('no_change')
    assert quiet['status'] == 'inconclusive'
    again, output = runs('worse')
    assert (again['status'], again['reason'], again['previous_run']) == ('blocked', 'candidate_already_regressed', first_output.name)
    assert not (output / 'evaluation-0').exists()
    assert again['incremental']['previous']['candidate_regressed'] is None  # the quiet run proposed no candidate to measure


def test_a_different_candidate_in_between_does_not_make_the_next_run_forget_a_regression(runs):
    first, first_output = runs('worse', harness='good')
    other, _ = runs('ok')  # a passing candidate on the same baseline
    assert other.get('reason') != 'candidate_already_regressed'
    again, _ = runs('worse')
    assert (again['status'], again['reason'], again['previous_run']) == ('blocked', 'candidate_already_regressed', first_output.name)


def test_a_regressed_candidate_applied_by_hand_after_a_blocked_repeat_is_still_flagged(runs):
    runs('worse', harness='good')
    runs('worse')  # blocked as a repeat; its receipt holds no measurement of its own
    edit(runs, 'worse')
    third, _ = runs('ok')
    block = third['incremental']
    assert block['disposition'] == 'applied' and block['previous']['candidate_regressed'] is True
    assert block['flags'] == ['regressed_candidate_applied']


def test_a_regressed_candidate_applied_by_hand_is_flagged_and_evaluated_against_its_new_baseline(runs):
    runs('worse', harness='good')
    edit(runs, 'worse')  # the blocked candidate adopted anyway
    second, _ = runs('ok')
    block = second['incremental']
    assert block['disposition'] == 'applied' and block['flags'] == ['regressed_candidate_applied']
    assert second['status'] != 'blocked' or second['reason'] != 'candidate_already_regressed'
    assert block['stages']['trials']['action'] == 'rerun' and block['fresh']['baseline_score'] < 1


def test_a_hand_edit_between_runs_is_an_edit_and_never_applied_or_ignored(runs):
    runs('ok', harness='bad')
    edit(runs, 'written by a person\n')
    second, _ = runs('ok')
    block = second['incremental']
    assert block['disposition'] == 'edited' and block['harness']['edited'] == dict(count=1, paths=['AGENTS.md'])
    assert block['harness']['applied']['count'] == block['harness']['ignored']['count'] == 0
    assert block['previous']['candidate_regressed'] is False


def test_an_edit_does_not_let_a_regressed_candidate_block_a_different_pair(runs):
    runs('worse', harness='good')
    second, _ = runs('worse', harness='good\n')  # another baseline: one more byte, still passing; the earlier measurement does not cover this pair
    assert (second['status'], second['reason']) == ('blocked', 'candidate_regressed')
    assert second['incremental']['disposition'] == 'edited' and (Path(second['output']) / 'investigation.json').is_file()


def test_a_run_without_a_frozen_candidate_still_reports_the_harness_delta(runs):
    first, _ = runs('no_change', harness='bad')
    assert first['status'] == 'inconclusive'
    edit(runs, 'bad plus a line\n')
    block = runs('no_change')[0]['incremental']
    assert block['disposition'] == 'no_candidate' and block['previous']['candidate_frozen'] is False
    assert block['previous']['candidate_regressed'] is None
    assert block['harness']['changed_since_previous_baseline'] is True and block['harness']['edited']['paths'] == ['AGENTS.md']


def test_an_unreadable_previous_record_is_unknown_and_never_a_first_run(runs):
    _, first_output = runs('ok', harness='bad')
    (first_output / 'harness-states.json').write_text('{not json')
    second, _ = runs('ok')
    assert second['incremental']['status'] == 'unavailable' and second['incremental']['reason'] == 'previous_run_unreadable'
    assert second['status'] == 'inconclusive' and 'disposition' not in second['incremental']


def test_an_unreadable_previous_receipt_leaves_the_outcome_unknown_but_not_the_harness_delta(runs):
    _, first_output = runs('ok', harness='bad')
    (first_output / 'workflow.json').write_text('{not json')
    block = runs('ok')[0]['incremental']
    assert block['disposition'] == 'ignored' and block['previous']['status'] == 'unknown'
    assert block['previous']['candidate_regressed'] is None and block['historical'] is None


def test_the_cli_exit_code_and_receipt_name_the_repeated_regression(runs, tmp_path, capsys):
    runs('worse', harness='good')
    base = runs.base
    code = main(['improve', str(base.project), '--agent', 'codex', '--codex', str(base.agent), '--claude', 'missing', '--timeout', '30'])
    receipt = json.loads(capsys.readouterr().out)
    assert (code, receipt['status'], receipt['reason']) == (3, 'blocked', 'candidate_already_regressed')
    assert receipt['exit_receipt']['stage'] == 'preparation' and receipt['incremental']['disposition'] == 'ignored'
