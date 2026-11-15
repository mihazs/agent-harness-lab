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
from agent_harness_lab.evaluation import before_after
from agent_harness_lab.evidence_label import grader_fingerprint
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

        def go(original, mode='ok', agent=None, **options):
            (base.project / 'AGENTS.md').write_text(original)
            (base.project / 'mode').write_text(mode)
            if agent is not None: base.agent.write_text(agent)
            args = base.args()
            for name, value in options.items(): setattr(args, name, value)
            result = improve(args)
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
    assert arms['baseline']['bank_sha256'] == before_after.bank_sha256(json.loads(manifest.read_text()), manifest.parent)
    assert arms['baseline']['grader_sha256'] == grader_fingerprint(manifest, portable=True)
    assert all(len(arms['baseline'][name]) == 64 for name in ('grader_sha256', 'seed_schedule_sha256', 'identity_sha256'))
    assert arms['baseline']['budget_share']['trials'] == 3 and len(arms['baseline']['runtime_sha256']) == 64


def test_the_same_inputs_have_the_same_identity_in_another_run_folder(run):
    first, first_output, _ = run('good', 'worse')
    second, second_output, _ = run('good', 'worse')
    assert first_output != second_output
    assert first['before_after']['frozen_inputs']['baseline'] == second['before_after']['frozen_inputs']['baseline']


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


@pytest.mark.parametrize('margin, regressed', [
    pytest.param(0.0, True, id='no_margin_is_the_default_and_any_net_loss_blocks'),
    pytest.param(0.5, True, id='loss_beyond_the_margin'),
    pytest.param(0.66, True, id='margin_just_under_the_observed_loss'),
    pytest.param(0.67, False, id='margin_just_over_the_observed_loss'),
    pytest.param(0.99, False, id='margin_tolerating_nearly_everything'),
])
def test_a_declared_margin_tolerates_a_loss_inside_it_and_blocks_one_beyond_it(run, margin, regressed):
    result, output, _ = run('good', 'worse', regression_margin=margin)  # two of three tasks flip to fail: the paired delta is -2/3
    guard = result['before_after']
    assert (guard['margin'], guard['adoption_blocked']) == (margin, regressed)
    assert guard['verdict'] == ('candidate_regressed' if regressed else 'inconclusive')
    assert (result['reason'], (output / 'investigation.json').exists()) == (('candidate_regressed', True) if regressed else ('evaluation_run_failed', False))


def test_a_declared_margin_also_raises_the_bar_for_improved(run):
    result, _, _ = run('bad', regression_margin=0.5)
    assert result['before_after']['margin'] == 0.5 and result['before_after']['verdict'] == 'inconclusive'


@pytest.mark.parametrize('value', [
    pytest.param('-0.1', id='negative'), pytest.param('1', id='one'), pytest.param('nan', id='nan'), pytest.param('abc', id='word'), pytest.param('', id='empty')])
def test_an_unsupported_margin_is_an_input_error_before_any_run(tmp_path, capsys, value):
    with ExitStack() as cleanups:
        base = workflow_fixture.WorkflowTests(cleanups.callback)
        with pytest.raises(SystemExit) as stopped:
            main(['improve', str(base.project), '--agent', 'codex', '--codex', str(base.agent), '--regression-margin', value, '--output', str(tmp_path / 'run')])
    assert stopped.value.code == 2 and 'margin' in capsys.readouterr().err and not (tmp_path / 'run').exists()


def test_a_margin_cannot_be_combined_with_a_fixed_bank_request_that_declares_its_own(tmp_path, capsys):
    with ExitStack() as cleanups:
        base = workflow_fixture.WorkflowTests(cleanups.callback)
        request = tmp_path / 'request.json'
        request.write_text('{}')
        with pytest.raises(SystemExit) as stopped:
            main(['improve', str(base.project), '--regression-margin', '0.1', '--experiment-request', str(request), '--output', str(tmp_path / 'run')])
    assert stopped.value.code == 2 and 'family_margin' in capsys.readouterr().err and not (tmp_path / 'run').exists()


def test_the_cli_carries_the_declared_margin_into_the_receipt(tmp_path, capsys):
    with ExitStack() as cleanups:
        base = workflow_fixture.WorkflowTests(cleanups.callback)
        base.agent.write_text(WORSE)
        (base.project / 'AGENTS.md').write_text('good')
        (base.project / 'mode').write_text('worse')
        code = main(['improve', str(base.project), '--agent', 'codex', '--codex', str(base.agent), '--claude', 'missing',
                     '--timeout', '30', '--regression-margin', '0.8', '--output', str(tmp_path / 'run')])
    receipt = json.loads(capsys.readouterr().out)
    assert (code, receipt['reason'], receipt['before_after']['margin'], receipt['before_after']['adoption_blocked']) == (1, 'evaluation_run_failed', 0.8, False)
