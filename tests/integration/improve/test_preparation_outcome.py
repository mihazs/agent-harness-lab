"""Preparation outcome (#822): a run that spent preparation and evaluated nothing says what preparation returned.

Every cell builds a synthetic project and recorded sessions under tmp_path with pinned dates and a scripted fake runtime.
"""
import pytest

from tests.support.first_use_history import Home, assert_candidate_or_typed_reason, improve


@pytest.fixture
def home(tmp_path, monkeypatch):
    return Home(tmp_path, monkeypatch)


@pytest.mark.xfail(strict=True, reason='#822: the receipt does not say what the preparation stage returned')
def test_recurring_failures_with_static_findings_that_spend_preparation_say_what_preparation_returned(home):
    """The field failure: usable history and static findings, preparation spent budget, nothing was evaluated."""
    (home.project/'AGENTS.md').write_text('synthetic line\n'*2000)  # an over-long instruction file is a static convention finding
    (home.project/'.git').mkdir()  # a Git project root settles the Codex instruction totals the line convention needs
    for name in ('a', 'b', 'c'): home.codex(name)
    code, receipt, err = improve(home)
    assert receipt['analysis']['findings'] > 0 and home.model_calls == 1
    assert_candidate_or_typed_reason(receipt)
    assert receipt['spend_label'] == 'spent_without_evaluation' and 'label=spent_without_evaluation' in err
    assert receipt['preparation']['outcome'] in ('hypothesis_supplied', 'no_change_supported', 'no_hypothesis', 'invalid_proposal')
