"""The first-use scenario matrix (#803) and the stress matrix (#807) re-run with the loop as the default (#817).

No cell may end as a bare `no_change`, a silent single pass or an untyped error: each ends with an evidenced candidate or a typed
reason with a remediation, and its receipt carries a `loop` block that says whether the loop ran, did not start or was not used and why.
A project with no pinned bank has no frozen bank to drive, so its default run makes the labelled single pass; a project with a pinned
bank and a history that has no outcome evidence stops before the loop with `loop.status: not_started`. Every project is synthetic.
"""
from contextlib import ExitStack
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_harness_lab.loop import entry
from tests.support import loop_improve as li
from tests.support.first_use_history import Home, assert_candidate_or_typed_reason, improve


@pytest.fixture
def home(tmp_path, monkeypatch):
    return Home(tmp_path, monkeypatch)


def assert_labelled_single_pass(receipt):
    assert receipt['loop'] == entry.not_used('loop_requires_frozen_bank')
    assert receipt['automatic_promotion'] is False and receipt['changed_files'] == []


def test_recurring_failures_without_outcomes_end_typed_spend_nothing_and_say_the_loop_was_not_used(home):
    for name in ('a', 'b', 'c'): home.codex(name)
    code, receipt, _ = improve(home)
    assert_candidate_or_typed_reason(receipt)
    assert (code, receipt['status'], receipt['reason'], home.model_calls) == (3, 'blocked', 'no_outcome_evidence', 0)
    assert_labelled_single_pass(receipt)


@pytest.mark.parametrize('status', [0, None], ids=['only_successes', 'every_outcome_unknown'])
def test_history_without_findings_is_labelled_and_never_called_fine_in_default_mode(home, status):
    for name in ('a', 'b', 'c'): home.codex(name, status=status)
    code, receipt, err = improve(home)
    assert receipt['reason'] == 'no_change_evidence_insufficient' and receipt['reason'] != 'no_change_harness_supported'
    assert receipt['spend_label'] == 'spent_without_evaluation' and 'label=spent_without_evaluation' in err
    assert_candidate_or_typed_reason(receipt)
    assert_labelled_single_pass(receipt)


def test_a_focus_that_reaches_preparation_still_ends_with_its_labelled_unevaluated_spend_in_default_mode(home):
    for name in ('a', 'b', 'c'): home.codex(name)
    code, receipt, _ = improve(home, '--focus', 'synthetic hypothesis')
    assert home.model_calls == 1 and receipt['status'] == 'inconclusive' and receipt['spend_label'] == 'spent_without_evaluation'
    assert_candidate_or_typed_reason(receipt)
    assert_labelled_single_pass(receipt)


def test_sessions_under_another_path_end_with_the_existing_typed_reason_and_a_labelled_single_pass(home):
    for name in ('a', 'b', 'c'): home.codex(name, cwd=home.root/'elsewhere')
    code, receipt, _ = improve(home)
    assert (receipt['status'], receipt['reason'], home.model_calls) == ('blocked', 'history_no_matching_sessions', 0)
    assert receipt['remediation']
    assert_labelled_single_pass(receipt)


@pytest.mark.parametrize('flag', ['--accept-score', '--max-rounds'], ids=['accept-score', 'max-rounds'])
def test_a_loop_flag_without_a_pinned_bank_is_blocked_and_never_a_silent_single_pass_even_with_history(home, flag):
    for name in ('a', 'b', 'c'): home.codex(name)
    value = '0.5' if flag == '--accept-score' else '2'
    code, receipt, _ = improve(home, flag, value)
    assert (code, receipt['status'], receipt['reason'], home.model_calls) == (3, 'blocked', 'loop_requires_frozen_bank', 0)
    assert receipt['loop']['status'] == 'not_started' and receipt['remediation']


def test_failures_with_a_pinned_bank_and_no_outcome_evidence_stop_before_the_loop_typed_with_the_reason(tmp_path, monkeypatch):
    with ExitStack() as stack:
        project = li.LoopProject(stack, monkeypatch, script={0: dict(harness=li.EVERYTHING)})
        root = project.fixture.root
        project.fixture.collect.stop()  # the workflow fixture stubs evidence collection; this cell reads the recorded sessions below
        monkeypatch.setenv('CODEX_HOME', str(root/'codex'))
        monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(root/'claude'))
        for name in ('a', 'b', 'c'): Home.codex(SimpleNamespace(root=root, project=project.project), name)
        code, receipt, _ = project.invoke('--history')
    assert (code, receipt['status'], receipt['reason']) == (3, 'blocked', 'no_outcome_evidence')
    assert_candidate_or_typed_reason(receipt)
    assert receipt['loop'] == entry.not_started('no_outcome_evidence') and project.calls() == []
    assert 'stop_criterion' not in receipt['loop'] and not list(Path(receipt['output']).glob('evaluation-*'))


def test_single_pass_in_the_matrix_keeps_the_previous_typed_end_state_and_says_it_is_deprecated(home):
    for name in ('a', 'b', 'c'): home.codex(name)
    code, receipt, _ = improve(home, '--single-pass')
    assert (receipt['status'], receipt['reason'], home.model_calls) == ('blocked', 'no_outcome_evidence', 0)
    assert receipt['loop'] == entry.not_used('single_pass_requested') and receipt['loop']['deprecated'] is True
    assert json.loads((Path(receipt['output'])/'workflow.json').read_text())['loop'] == receipt['loop']
