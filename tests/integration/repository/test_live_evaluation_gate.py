"""The manual live-evaluation workflow cannot run paid work from untrusted code, and refuses unbounded plans."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT/'scripts/live_evaluation_gate.py'
spec = importlib.util.spec_from_file_location('live_evaluation_gate', SCRIPT)
gate = importlib.util.module_from_spec(spec); spec.loader.exec_module(gate)

GOOD = {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main', 'INPUT_RUNTIME': 'claude',
        'INPUT_PROVENANCE': 'synthetic', 'INPUT_MAX_TRIALS': '20', 'INPUT_BUDGET_USD': '5', 'INPUT_PRICING_DATE': '2026-11-01',
        'INPUT_MAX_MINUTES': '60'}


def refused(**changes):
    return gate.refusals({**GOOD, **changes})


def test_a_bounded_trusted_manual_plan_is_accepted():
    assert refused() == []


@pytest.mark.parametrize('changes, text', [
    ({'GITHUB_EVENT_NAME': 'pull_request'}, 'manual'),
    ({'GITHUB_EVENT_NAME': 'schedule'}, 'manual'),
    ({'GITHUB_REF': 'refs/pull/7/merge'}, 'refs/heads/main'),
    ({'GITHUB_REF': 'refs/heads/feature'}, 'refs/heads/main'),
    ({'INPUT_RUNTIME': 'all'}, 'runtime'),
    ({'INPUT_PROVENANCE': 'private'}, 'provenance'),
    ({'INPUT_PROVENANCE': 'anonymized-private'}, 'provenance'),
    ({'INPUT_MAX_TRIALS': '0'}, 'max_trials'),
    ({'INPUT_MAX_TRIALS': '201'}, 'max_trials'),
    ({'INPUT_MAX_TRIALS': ''}, 'max_trials'),
    ({'INPUT_BUDGET_USD': '0'}, 'budget_usd'),
    ({'INPUT_BUDGET_USD': '100.01'}, 'budget_usd'),
    ({'INPUT_BUDGET_USD': 'nan'}, 'budget_usd'),
    ({'INPUT_BUDGET_USD': 'inf'}, 'budget_usd'),
    ({'INPUT_BUDGET_USD': '-1'}, 'budget_usd'),
    ({'INPUT_MAX_MINUTES': '361'}, 'max_minutes'),
    ({'INPUT_PRICING_DATE': ''}, 'pricing_date'),
    ({'INPUT_PRICING_DATE': 'last week'}, 'pricing_date')])
def test_a_missing_or_unbounded_precondition_is_refused(changes, text):
    assert any(text in line for line in refused(**changes))


def test_a_missing_input_is_refused_not_defaulted():
    env = {k: v for k, v in GOOD.items() if k != 'INPUT_BUDGET_USD'}
    assert any('budget_usd' in line for line in gate.refusals(env))


def write(directory, name, text):
    (directory/name).write_text(text)
    return directory


def test_the_repository_workflows_satisfy_the_trust_policy():
    assert gate.workflow_findings(ROOT/'.github/workflows') == []


def test_the_live_workflow_is_manual_and_budget_gated():
    text = (ROOT/'.github/workflows/live-evaluation.yml').read_text()
    assert 'secrets.' not in text
    assert all(name in text for name in ('budget_usd', 'max_trials', 'max_minutes', 'pricing_date'))


LIVE_OK = """on:
  workflow_dispatch:
permissions:
  contents: read
concurrency:
  group: live
jobs:
  gate:
    if: github.ref == 'refs/heads/main'
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false
"""


@pytest.mark.parametrize('name, text, needle', [
    ('tests.yml', 'on:\n  pull_request_target:\n', 'pull_request_target'),
    ('tests.yml', 'on:\n  pull_request:\njobs:\n  t:\n    env:\n      K: ${{ secrets.PROVIDER_KEY }}\n', 'provider-style secrets'),
    ('tests.yml', 'on:\n  pull_request:\njobs:\n  t:\n    uses: ./.github/workflows/live-evaluation.yml\n    secrets: inherit\n', 'provider-style secrets'),
    ('live-evaluation.yml', LIVE_OK.replace('workflow_dispatch:', 'workflow_dispatch:\n  schedule:\n    - cron: "0 0 * * *"'), 'only by workflow_dispatch'),
    ('live-evaluation.yml', LIVE_OK.replace('workflow_dispatch:', 'workflow_dispatch:\n  pull_request:'), 'only by workflow_dispatch'),
    ('live-evaluation.yml', LIVE_OK.replace("github.ref == 'refs/heads/main'", 'true'), 'trusted-ref'),
    ('live-evaluation.yml', LIVE_OK.replace('concurrency:\n  group: live\n', ''), 'concurrency'),
    ('live-evaluation.yml', LIVE_OK.replace('    timeout-minutes: 10\n', ''), 'timeout'),
    ('live-evaluation.yml', LIVE_OK.replace('persist-credentials: false', 'fetch-depth: 0'), 'persisted credentials'),
    ('live-evaluation.yml', LIVE_OK.replace('contents: read', 'contents: write'), 'read-only'),
    ('live-evaluation.yml', LIVE_OK.replace('group: live', 'group: live\n  cancel-in-progress: true'), 'must not cancel')])
def test_an_unsafe_workflow_is_reported(tmp_path, name, text, needle):
    assert any(needle in line for line in gate.workflow_findings(write(tmp_path, name, text)))


def test_a_safe_live_workflow_and_a_comment_mentioning_secrets_pass(tmp_path):
    write(tmp_path, 'live-evaluation.yml', '# secrets.X pull_request_target\n' + LIVE_OK)
    assert gate.workflow_findings(tmp_path) == []


def test_the_command_exits_2_with_reasons_and_prints_nothing_on_stdout():
    env = {**GOOD, 'GITHUB_REF': 'refs/pull/9/merge', 'PATH': ''}
    result = subprocess.run([sys.executable, str(SCRIPT), 'gate'], env=env, capture_output=True, text=True)
    assert result.returncode == 2 and 'refs/heads/main' in result.stderr and result.stdout == ''
