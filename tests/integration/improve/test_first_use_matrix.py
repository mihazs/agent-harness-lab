"""First-use scenario matrix (#803): usable history ends with an evidenced candidate or a typed reason with a remediation.

Every cell builds a synthetic project and recorded sessions under tmp_path with pinned dates and a counting fake runtime.
"""
import json
from contextlib import redirect_stderr, redirect_stdout
import io

import pytest

from agent_harness_lab import cli
from tests.support.first_use_history import Home


@pytest.fixture
def home(tmp_path, monkeypatch):
    return Home(tmp_path, monkeypatch)


def improve(home, *extra):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(['improve', str(home.project), '--history', '--agent', 'codex', '--codex', str(home.runtime),
                         '--timeout', '30', *extra])
    return code, json.loads(out.getvalue()), err.getvalue()


def candidates(receipt):
    return len(receipt.get('candidates') or []) + int('plan' in receipt)


def assert_candidate_or_typed_reason(receipt):
    """The first-use rule: an evidenced candidate, or a typed reason with a remediation that explains the findings per cause."""
    if candidates(receipt):
        assert all(row.get('evidence') for row in receipt.get('candidates', []))
        return
    assert isinstance(receipt.get('reason'), str) and receipt['reason'], receipt
    assert receipt.get('remediation'), receipt
    findings = receipt['analysis']['findings'] if 'analysis' in receipt else 0
    if findings:
        explained = receipt['findings_not_candidates']
        assert explained['reason'] == 'findings_not_actionable' and explained['findings'] == findings
        assert explained['causes'] and all(type(count) is int and 0 < count <= findings for count in explained['causes'].values())


def test_recurring_failures_without_outcomes_end_typed_and_spend_nothing(home):
    """The field case: findings across sessions, zero verified outcomes, no hypothesis source."""
    for name in ('a', 'b', 'c'): home.codex(name)
    code, receipt, _ = improve(home)
    assert receipt['analysis']['findings'] > 0
    assert_candidate_or_typed_reason(receipt)
    assert (receipt['status'], receipt['reason']) == ('blocked', 'no_outcome_evidence')
    assert 'no_outcome_evidence' in receipt['findings_not_candidates']['causes']
    steps = receipt['next_steps']  # the evidence that would unlock a candidate, ranked, each with the command to run
    assert steps and [row['rank'] for row in steps] == list(range(1, len(steps) + 1)) and all(row['command'] for row in steps)
    assert home.model_calls == 0 and 'spend_label' not in receipt
    assert receipt['changed_files'] == [] and receipt['automatic_promotion'] is False


def test_a_single_failure_without_outcomes_ends_typed_and_spends_nothing(home):
    home.codex('only', calls=1)
    code, receipt, _ = improve(home)
    assert receipt['analysis']['findings'] == 1
    assert_candidate_or_typed_reason(receipt)
    assert (receipt['reason'], home.model_calls) == ('no_outcome_evidence', 0)


def test_failures_beside_a_user_focus_still_run_preparation_and_label_the_unevaluated_spend(home):
    for name in ('a', 'b', 'c'): home.codex(name)
    code, receipt, err = improve(home, '--focus', 'synthetic hypothesis')
    assert home.model_calls == 1 and receipt['status'] == 'no_change'
    assert receipt['spend_label'] == 'spent_without_evaluation'
    assert receipt['spent_without_evaluation']['provider_reported_spend'] == 'unverified'
    assert 'label=spent_without_evaluation' in err
    assert_candidate_or_typed_reason(receipt)
    assert receipt['findings_not_candidates']['causes']['proposer_returned_no_change'] == receipt['analysis']['findings']


def test_a_candidate_identical_to_the_baseline_still_explains_each_finding_and_labels_the_spend(home):
    for name in ('a', 'b', 'c'): home.codex(name)
    home.proposes_an_unedited_candidate()
    code, receipt, err = improve(home, '--focus', 'synthetic hypothesis')
    assert home.model_calls == 1 and (receipt['status'], receipt['reason']) == ('no_change', 'candidate_identical_to_baseline')
    assert_candidate_or_typed_reason(receipt)
    assert receipt['findings_not_candidates']['causes']['proposer_returned_no_change'] == receipt['analysis']['findings']
    assert receipt['spend_label'] == 'spent_without_evaluation' and 'label=spent_without_evaluation' in err


@pytest.mark.parametrize('status', [0, None], ids=['only_successes', 'every_outcome_unknown'])
def test_history_without_findings_that_spent_preparation_is_labelled_and_never_called_fine(home, status):
    for name in ('a', 'b', 'c'): home.codex(name, status=status)
    code, receipt, err = improve(home)
    assert receipt['analysis']['findings'] == 0 and receipt['analysis']['events'] > 0
    assert receipt['reason'] == 'no_change_evidence_insufficient' and receipt['reason'] != 'no_change_harness_supported'
    assert receipt['spend_label'] == 'spent_without_evaluation' and 'label=spent_without_evaluation' in err
    assert_candidate_or_typed_reason(receipt)


def test_explicit_events_with_failures_and_no_outcomes_still_reach_the_proposer(home):
    """Explicit events never read evaluation results, so no outcome-evidence remediation could unlock a block there."""
    for name in ('a', 'b', 'c'): home.codex(name)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        cli.main(['improve', str(home.project), '--agent', 'codex', '--codex', str(home.runtime), '--timeout', '30',
                  '--events', str(home.root/'codex/sessions/a.jsonl'), '--format', 'codex'])
    receipt = json.loads(out.getvalue())
    assert receipt['analysis']['findings'] > 0
    assert receipt.get('reason') != 'no_outcome_evidence' and home.model_calls == 1, receipt


def test_a_verified_graded_outcome_lifts_the_gate_so_the_remediation_can_work(home):
    for name in ('a', 'b', 'c'): home.codex(name)
    home.graded_evaluation()
    code, receipt, _ = improve(home)
    assert receipt['analysis']['findings'] > 0 and receipt.get('reason') != 'no_outcome_evidence'
    assert home.model_calls == 1


def test_failures_beside_only_unknown_outcomes_end_typed_and_never_count_the_unknown_as_evidence(home):
    for name in ('a', 'b', 'c'): home.codex(name)
    home.ungraded_evaluation()
    code, receipt, _ = improve(home)
    assert receipt['analysis']['evaluation_records'] == 1 and receipt['analysis']['findings'] > 0
    assert_candidate_or_typed_reason(receipt)
    assert (receipt['reason'], home.model_calls) == ('no_outcome_evidence', 0)


def test_sessions_outside_the_verified_version_range_end_typed_and_say_the_capture_is_unverified(home):
    for number in range(3): home.claude(number)
    code, receipt, _ = improve(home, '--source', 'claude')
    assert receipt['analysis']['findings'] > 0
    assert_candidate_or_typed_reason(receipt)
    assert receipt['findings_not_candidates']['causes']['capture_unverified'] == receipt['analysis']['findings']
    assert home.model_calls == 0


def test_sessions_under_another_path_end_with_the_existing_typed_reason_and_remediation(home):
    for name in ('a', 'b', 'c'): home.codex(name, cwd=home.root/'elsewhere')
    code, receipt, _ = improve(home)
    assert (receipt['status'], receipt['reason']) == ('blocked', 'history_no_matching_sessions')
    assert receipt['remediation'] and home.model_calls == 0


def test_mixed_runtimes_with_failures_end_typed_and_spend_nothing(home):
    home.codex('a'); home.codex('b'); home.claude(1); home.claude(2)
    code, receipt, _ = improve(home)
    assert {row['format'] for row in json.loads(open(receipt['output'] + '/analysis.json').read())['sources']} >= {'codex', 'claude'}
    assert_candidate_or_typed_reason(receipt)
    assert (receipt['reason'], home.model_calls) == ('no_outcome_evidence', 0)


def test_failures_with_static_convention_findings_stage_a_labelled_hypothesis_and_spend_one_preparation_call(home):
    (home.project/'AGENTS.md').write_text('synthetic line\n'*2000)  # an over-long instruction file is a static convention finding
    (home.project/'.git').mkdir()  # a Git project root settles the Codex instruction totals the line convention needs
    for name in ('a', 'b', 'c'): home.codex(name)
    code, receipt, _ = improve(home)
    hypothesis = receipt['audit_hypothesis']
    assert hypothesis['hypothesis_source'] == 'static_convention' and hypothesis['authority'] == 'none' and hypothesis['finding_id']
    assert receipt['reason'] != 'no_outcome_evidence' and home.model_calls == 1
    assert receipt['provenance']['agent_supplied_hypothesis'][0]['source'] == 'static_audit'
    assert receipt['spend_label'] == 'spent_without_evaluation'  # the fake proposer declines, so no trial was evaluated
