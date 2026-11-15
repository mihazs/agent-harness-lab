"""Preparation outcome (#822): a run that spent preparation and evaluated nothing says what preparation returned.

Every cell builds a synthetic project and recorded sessions under tmp_path with pinned dates and a scripted fake runtime.
"""
import json
from pathlib import Path

import pytest

from tests.support.first_use_history import Home, assert_candidate_or_typed_reason, improve

SENTINEL = 'synthetic-private-sentence-7f3a'  # free text a proposer might write; it must never reach a receipt, report or terminal
PASSED = ['skill_routing_collision', 'instruction_procedure_section_detected']


@pytest.fixture
def home(tmp_path, monkeypatch):
    return Home(tmp_path, monkeypatch)


def staged(home):
    """Recurring failures across three sessions plus an over-long instruction file, which stages a static convention."""
    (home.project/'AGENTS.md').write_text('synthetic line\n'*2000)
    (home.project/'.git').mkdir()  # a Git project root settles the Codex instruction totals the line convention needs
    for name in ('a', 'b', 'c'): home.codex(name)


def private_text(receipt, err):
    """Everything a run shows or keeps beside its receipt."""
    return json.dumps(receipt) + err + (Path(receipt['output'])/'report.md').read_text()


def test_recurring_failures_with_static_findings_that_spend_preparation_say_what_preparation_returned(home):
    """The field failure: usable history and static findings, preparation spent budget, nothing was evaluated."""
    staged(home)
    code, receipt, err = improve(home)
    assert receipt['analysis']['findings'] > 0 and home.model_calls == 1
    assert_candidate_or_typed_reason(receipt)
    assert receipt['spend_label'] == 'spent_without_evaluation' and 'label=spent_without_evaluation' in err
    assert receipt['preparation']['outcome'] == 'no_hypothesis'  # the scripted proposer named no typed reason


def test_a_typed_no_change_proposal_reaches_the_receipt_the_terminal_and_the_report(home):
    staged(home)
    home.scripts(dict(status='no_change', reason=SENTINEL, reason_codes=['no_verified_outcome', 'pattern_not_recurring'],
                      missing_evidence=['verified_outcomes'], baseline_checks_passed=PASSED))
    code, receipt, err = improve(home)
    block = receipt['preparation']
    assert (block['outcome'], block['reason_codes'], block['missing_evidence']) == (
        'no_change_supported', ['no_verified_outcome', 'pattern_not_recurring'], ['verified_outcomes'])
    assert block['baseline_checks_passed'] == dict(count=2, classes=sorted(PASSED))
    assert (block['declared_by'], block['controller_verified'], block['unrecognized']) == ('proposer', False, 0)
    assert receipt['spend_label'] == 'spent_without_evaluation' and receipt['status'] == 'no_change'
    lines = [line for line in err.splitlines() if line.startswith('ahl: preparation ')]
    assert lines == ['ahl: preparation outcome=no_change_supported reasons=no_verified_outcome,pattern_not_recurring '
                     'missing_evidence=verified_outcomes baseline_checks_passed=2']
    assert err.index(lines[0]) < err.index('ahl: outcome=')
    report = (Path(receipt['output'])/'report.md').read_text()
    assert '## Preparation outcome' in report and '`no_change_supported`' in report and '`no_verified_outcome`' in report


def test_a_no_change_proposal_without_typed_reasons_is_no_hypothesis_and_never_support(home):
    staged(home)
    home.scripts(dict(status='no_change', reason=SENTINEL))
    code, receipt, err = improve(home)
    block = receipt['preparation']
    assert (block['outcome'], block['reason_codes'], block['missing_evidence']) == ('no_hypothesis', [], [])
    assert block['baseline_checks_passed'] == dict(count=0, classes=[])


def test_a_candidate_that_changes_the_instructions_is_a_hypothesis_supplied(home):
    staged(home)
    home.scripts(dict(status='candidate', mechanisms=[], falsifiers=[]), edit=True)
    code, receipt, err = improve(home)
    assert receipt['preparation']['outcome'] == 'hypothesis_supplied'
    assert receipt['preparation']['reason_codes'] == []


def test_a_candidate_identical_to_the_baseline_is_no_hypothesis(home):
    staged(home)
    home.proposes_an_unedited_candidate()
    code, receipt, err = improve(home)
    assert receipt['reason'] == 'candidate_identical_to_baseline'
    assert receipt['preparation']['outcome'] == 'no_hypothesis'
    assert receipt['spend_label'] == 'spent_without_evaluation'


@pytest.mark.parametrize('script', [
    pytest.param(dict(omit=True), id='no_proposal_file'),
    pytest.param(dict(raw='{not json'), id='malformed_json'),
    pytest.param(dict(raw='["no_change"]'), id='not_an_object'),
    pytest.param(dict(proposal=dict(status='maybe', reason=SENTINEL)), id='unknown_status'),
    pytest.param(dict(proposal=dict(reason_codes=['baseline_conforms'])), id='missing_status'),
])
def test_an_unusable_proposal_ends_invalid_proposal_with_the_label_and_no_declared_fields(home, script):
    staged(home)
    home.scripts(**script)
    code, receipt, err = improve(home)
    assert (receipt['status'], receipt['preparation']['outcome']) == ('invalid_proposal', 'invalid_proposal')
    assert receipt['preparation']['reason_codes'] == [] and receipt['preparation']['unrecognized'] == 0
    assert receipt['spend_label'] == 'spent_without_evaluation' and code != 0
    assert 'ahl: preparation outcome=invalid_proposal ' in err


def test_free_text_and_names_outside_the_vocabulary_never_reach_the_receipt_the_report_or_the_terminal(home):
    staged(home)
    home.scripts(dict(status='no_change', reason=SENTINEL, reason_codes=['no_verified_outcome', SENTINEL, '/private/' + SENTINEL, 7],
                      missing_evidence=['verified_outcomes', SENTINEL], baseline_checks_passed={SENTINEL: 1},
                      mechanisms=[SENTINEL], falsifiers=[SENTINEL], next=SENTINEL))
    code, receipt, err = improve(home)
    block = receipt['preparation']
    assert block['reason_codes'] == ['no_verified_outcome'] and block['missing_evidence'] == ['verified_outcomes']
    assert block['baseline_checks_passed']['classes'] == [] and block['unrecognized'] == 3 + 1 + 1
    assert SENTINEL not in private_text(receipt, err)


def test_an_oversized_declared_list_is_bounded_and_counted_without_being_echoed(home):
    staged(home)
    home.scripts(dict(status='no_change', reason_codes=['baseline_conforms'] + [f'{SENTINEL}-{number}' for number in range(5000)]))
    code, receipt, err = improve(home)
    block = receipt['preparation']
    assert block['reason_codes'] == ['baseline_conforms'] and block['unrecognized'] == 5000
    assert SENTINEL not in private_text(receipt, err)


def test_a_missing_evidence_id_the_brief_did_not_list_is_unrecognized(home):
    staged(home)
    home.scripts(dict(status='no_change', reason_codes=['evidence_ambiguous'], missing_evidence=['event_order']))
    code, receipt, err = improve(home)
    listed = {row['id'] for row in json.loads((Path(receipt['output'])/'analysis-brief.json').read_text())['missing_evidence']}
    assert 'event_order' not in listed
    assert receipt['preparation']['missing_evidence'] == [] and receipt['preparation']['unrecognized'] == 1


def test_a_run_that_never_called_the_model_carries_no_preparation_block(home):
    for name in ('a', 'b', 'c'): home.codex(name)
    code, receipt, err = improve(home)
    assert (receipt['reason'], home.model_calls) == ('no_outcome_evidence', 0)
    assert 'preparation' not in receipt and 'ahl: preparation ' not in err

