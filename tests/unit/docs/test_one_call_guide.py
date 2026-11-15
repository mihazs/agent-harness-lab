"""docs/one-call-improvement.md describes the pinned project bank path that one_call.preflight implements."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GUIDE = ' '.join((ROOT/'docs/one-call-improvement.md').read_text().split())
PREFLIGHT = (ROOT/'agent_harness_lab/one_call.py').read_text()

PINNED_BANK_REASONS = (
    'configured_bank_requires_cooperative_runtime',
    'configured_bank_cannot_combine_with_managed_experiment',
    'configured_managed_bank_requires_controlled_workflow',
    'configured_bank_requires_development_regression_and_heldout',
    'configured_bank_shared_trial_budget_exhausted',
)


@pytest.mark.parametrize('claim', [
    pytest.param('unintegrated', id='project-banks-called-unintegrated'),
    pytest.param('pinned target-bank integration (#142)', id='pinned-bank-integration-listed-as-incomplete')])
def test_guide_does_not_call_the_implemented_pinned_project_bank_path_unintegrated(claim):
    assert claim not in GUIDE


@pytest.mark.parametrize('reason', [pytest.param(reason, id=reason) for reason in PINNED_BANK_REASONS])
def test_guide_names_each_preflight_reason_that_stops_a_pinned_project_bank(reason):
    assert f"return '{reason}'" in PREFLIGHT
    assert f'`{reason}`' in GUIDE
