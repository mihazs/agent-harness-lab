"""Default `ahl improve` before/after guard on synthetic cooperative runs only (#804).

The same fake agent plays proposer, construction and trials; a candidate that is worse than the baseline must end as the typed
`candidate_regressed` with an investigation artifact, never as the bare `evaluation_failed` a retry loop re-runs.
"""
import hashlib
import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.workflow import improve
from tests.integration.workflow import test_workflow as workflow_fixture

# Same synthetic agent; one added mode keeps the candidate failing on both attempts.
WORSE = workflow_fixture.FAKE.replace("'worse' if mode=='refine' and c['attempt']==0",
                                      "'worse' if mode=='worse' or mode=='refine' and c['attempt']==0")
assert WORSE != workflow_fixture.FAKE
# Candidate fails attempt 0; the proposer then stops attempt 1 before any evaluation.
LATE_STOP = WORSE.replace("if mode=='no_change':", "if mode=='no_change' or mode=='late_stop' and c['attempt']==1:").replace(
    "'worse' if mode=='worse' or", "'worse' if mode in ('worse','late_stop') or")
assert LATE_STOP.count('late_stop') == 2


@pytest.fixture
def run():
    """Run the real workflow entry on a synthetic project whose baseline is `original` and whose proposer follows `mode`."""
    with ExitStack() as cleanups:
        base = workflow_fixture.WorkflowTests(cleanups.callback)
        base.agent.write_text(WORSE)

        def go(original, mode='ok', agent=None):
            (base.project / 'AGENTS.md').write_text(original)
            (base.project / 'mode').write_text(mode)
            if agent is not None: base.agent.write_text(agent)
            result = improve(base.args())
            return result, Path(result['output']), base
        yield go


def test_candidate_worse_than_the_baseline_blocks_adoption_with_a_typed_reason_and_an_investigation(run):
    result, output, base = run('good', 'worse')
    assert (result['status'], result['reason']) == ('blocked', 'candidate_regressed')
    assert 'plan' not in result and 'recommendation' not in result
    assert (base.project / 'AGENTS.md').read_text() == 'good'
    guard = result['before_after']
    assert (guard['verdict'], guard['adoption_blocked']) == ('candidate_regressed', True)
    assert guard['delta']['mean'] < 0 and guard['delta']['low'] <= guard['delta']['mean'] <= guard['delta']['high']
    assert guard['scores']['baseline']['mean'] > guard['scores']['candidate']['mean']
    assert guard['pairs'] == dict(eligible=3, unknown=0, flipped_to_fail=2, flipped_to_pass=0, unchanged=1, repeats_per_task_arm=1)
    path = output / 'investigation.json'
    assert result['investigation']['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    artifact = json.loads(path.read_text())
    assert sorted(row['task'] for row in artifact['flipped']['to_fail']) == ['development', 'heldout']
    assert {row['task']: row['delta'] for row in artifact['per_task']} == dict(development=-1, regression=0, heldout=-1)
    assert artifact['cause']['class'] == 'candidate_caused' == result['investigation']['cause']
    assert artifact['changes'] == dict(grader='unchanged', frozen_inputs='unchanged', budget='identical')


def test_a_candidate_that_is_not_worse_keeps_the_ordinary_failed_evaluation(run):
    result, output, _ = run('bad', 'worse')  # both arms fail the same tasks: failing is not regressing
    assert (result['status'], result['reason']) == ('evaluation_failed', 'evaluation_run_failed')
    assert result['before_after']['verdict'] == 'inconclusive' and result['before_after']['adoption_blocked'] is False
    assert result['before_after']['delta']['mean'] == 0
    assert not (output / 'investigation.json').exists() and 'investigation' not in result


def test_a_candidate_that_helps_is_never_labelled_improved_on_three_tasks(run):
    result, output, _ = run('bad')
    assert (result['status'], result['recommendation']) == ('inconclusive', 'candidate_requires_independent_certification')
    guard = result['before_after']
    assert guard['verdict'] == 'inconclusive' and 'interval_not_above_margin' in guard['reasons']
    assert guard['delta']['mean'] > 0 and guard['delta']['low'] < 0 < guard['delta']['high']  # two flips in three tasks are within noise
    assert guard['adoption_blocked'] is False and not (output / 'investigation.json').exists()


def test_a_tie_with_the_baseline_stays_a_tie(run):
    result, _, _ = run('good')
    assert (result['status'], result['reason']) == ('no_change', 'evaluation_no_improvement')
    assert (result['before_after']['verdict'], result['before_after']['delta']['mean']) == ('inconclusive', 0)


def test_both_arms_record_the_same_frozen_inputs_identity(run):
    result, output, _ = run('good', 'worse')
    guard = result['before_after']
    arms = guard['frozen_inputs']
    assert arms['baseline'] == arms['candidate'] and arms['comparison'] == dict(status='identical', mismatched=[], unknown=[])
    manifest = Path(guard['bindings']['evaluation']).parent / 'preparation' / f"evaluation-{guard['bindings']['attempt']}" / 'manifest.json'
    assert arms['baseline']['bank_sha256'] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert all(len(arms['baseline'][name]) == 64 for name in ('grader_sha256', 'seed_schedule_sha256', 'identity_sha256'))
    assert arms['baseline']['budget_share']['trials'] == 3 and len(arms['baseline']['runtime_sha256']) == 64


def test_an_earlier_attempt_regression_does_not_relabel_a_run_whose_final_attempt_never_evaluated(run):
    result, output, _ = run('good', 'late_stop', agent=LATE_STOP)
    assert (result['status'], result['reason']) == ('no_change', 'no_change_evidence_insufficient')
    assert result['before_after']['verdict'] == 'candidate_regressed'
    assert result['before_after']['bindings']['describes_final_attempt'] is False
    assert 'investigation' not in result and not (output / 'investigation.json').exists()


def test_a_guard_failure_never_changes_the_run_result(run):
    with patch('agent_harness_lab.evaluation.before_after.decide', side_effect=RuntimeError('synthetic')):
        result, output, _ = run('good', 'worse')
    assert (result['status'], result['reason']) == ('evaluation_failed', 'evaluation_run_failed')
    assert result['before_after'] == dict(status='unavailable', reason='RuntimeError', adoption_authority=False)
    assert not (output / 'investigation.json').exists()


def test_the_cli_exit_code_and_receipt_name_the_regression(tmp_path, capsys, monkeypatch):
    with ExitStack() as cleanups:
        base = workflow_fixture.WorkflowTests(cleanups.callback)
        base.agent.write_text(WORSE)
        (base.project / 'AGENTS.md').write_text('good')
        (base.project / 'mode').write_text('worse')
        code = main(['improve', str(base.project), '--agent', 'codex', '--codex', str(base.agent), '--claude', 'missing',
                     '--timeout', '30', '--output', str(tmp_path / 'run')])
    receipt = json.loads(capsys.readouterr().out)
    assert (code, receipt['status'], receipt['reason']) == (3, 'blocked', 'candidate_regressed')
    assert receipt['exit_receipt']['stage'] == 'trials' and 'investigation.json' in receipt['exit_receipt']['next_step']
