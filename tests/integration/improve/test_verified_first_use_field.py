"""Synthetic reproduction of first use with verified native history and static findings (#803)."""
import json
from pathlib import Path

import pytest

from tests.support.first_use_history import Home, assert_candidate_or_typed_reason, improve


@pytest.fixture
def home(tmp_path, monkeypatch):
    return Home(tmp_path, monkeypatch)


def test_verified_recurring_failures_and_static_findings_explain_no_candidate_and_spend(home):
    (home.project / '.git').mkdir()
    (home.project / 'AGENTS.md').write_text('synthetic instruction\n' * 2000)
    for number in range(3):
        home.claude(number, version='2.1.316')
    _, receipt, _ = improve(home, '--source', 'claude')
    assert receipt['analysis']['findings'] > 0
    analysis = json.loads((Path(receipt['output']) / 'analysis.json').read_text())
    assert len(analysis['sources']) == 3 and all(source['adapter'] == 'verified_native' for source in analysis['sources'])
    assert receipt['audit_hypothesis']['hypothesis_source'] == 'static_convention'
    assert_candidate_or_typed_reason(receipt)
    assert home.model_calls == 1 and receipt['spend_label'] == 'spent_without_evaluation'
