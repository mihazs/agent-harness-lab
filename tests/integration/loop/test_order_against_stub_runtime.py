"""The real loop controller over FrozenBankEvaluator and the stub agent runtime: order, identical frozen inputs, comparison_invalid, the #804 fixtures (#813).

The stub agent writes the artifact for the task numbers a snapshot's AGENTS.md lists, so a trial outcome is a function of the snapshot and
the task. The trial runner, the graders, the statistics and the paired comparison are the real ones. Every bank and snapshot is synthetic.
"""
import json
from pathlib import Path
import shutil

import pytest

from agent_harness_lab.evaluation import integrity
from agent_harness_lab.loop import controller
from agent_harness_lab.loop.errors import LoopError
from tests.support import frozen_bank as fb


def kinds(ledger):
    return [kind for kind, _ in ledger.events()]


def values(ledger, kind):
    return [payload['value'] for event, payload in ledger.events() if event == kind]


def play(tmp_path, stub, baseline, candidates, *, on_propose=None, **declaration):
    """The real controller over the stub runtime; returns the result, ledger, evaluator, proposer and the order of port calls."""
    spec, order = fb.declare(**declaration), []
    evaluator = stub.evaluator(spec)
    proposer = fb.Proposer(baseline, candidates, evaluator, order=order, on_propose=on_propose)
    result, ledger = fb.drive(tmp_path, fb.Recording(evaluator, order), proposer, spec)
    return result, ledger, evaluator, proposer, order


def test_the_baseline_is_evaluated_before_the_first_proposer_call_and_every_evaluation_runs_on_the_same_frozen_inputs(tmp_path, monkeypatch):
    opened = []
    real = integrity.digest
    monkeypatch.setattr(integrity, 'digest', lambda root, **limits: opened.append(Path(root).name) or real(root, **limits))
    stub = fb.StubRuntime(tmp_path, heldout=3)
    base, good = stub.snapshot('base', [0, 1]), stub.snapshot('good', range(8))
    result, ledger, evaluator, proposer, order = play(tmp_path, stub, base, [good], accept_score=0.3)
    assert order == ['evaluate', 'propose', 'evaluate'] and proposer.calls == [(base, 'm1')]
    assert kinds(ledger) == ['spec_frozen', 'baseline_evaluated', 'round_started', 'candidate_frozen', 'candidate_evaluated', 'decided', 'stopped']
    frozen = [payload['frozen_inputs_sha256'] for kind, payload in ledger.events() if kind == 'spec_frozen']
    ledger_inputs = [payload['evaluation']['inputs'] for kind, payload in ledger.events() if kind.endswith('_evaluated')]
    assert ledger_inputs == [result['frozen_inputs_sha256']]*2 and frozen == [result['frozen_inputs_sha256']]
    identities = [run['identity'] for run in evaluator.runs]
    for field in ('bank_sha256', 'grader_sha256', 'seed_schedule_sha256', 'budget_share', 'runtime_sha256'):
        assert len({json.dumps(identity[field]) for identity in identities}) == 1, field
    assert (result['outcome'], result['stop'], result['best']['snapshot']) == ('accepted', 'acceptance_score_reached', good)
    assert evaluator.runs[1]['guard']['verdict'] == 'improved' and evaluator.runs[1]['guard']['adoption_blocked'] is False
    assert not any(name.startswith('h') for name in opened) and not any((tmp_path/'bank/fixtures'/f'h{number}').exists() for number in range(3))


def test_a_candidate_that_is_no_better_than_the_baseline_is_inconclusive_and_the_loop_plateaus(tmp_path):
    stub = fb.StubRuntime(tmp_path)
    base = stub.snapshot('base', [0, 1])
    flips = [stub.snapshot('first', [0, 2]), stub.snapshot('second', [1, 2])]  # one root up and one down each time: the same score
    result, ledger, evaluator, _, _ = play(tmp_path, stub, base, flips, max_rounds=3, plateau_rounds=2, accept_score=0.9)
    assert (result['outcome'], result['stop'], result['best']['snapshot'], result['rounds']) == ('inconclusive', 'plateau', base, 2)
    assert values(ledger, 'decided') == ['inconclusive', 'inconclusive']
    assert [run['guard']['verdict'] for run in evaluator.runs[1:]] == ['inconclusive', 'inconclusive'] and [run['investigation'] for run in evaluator.runs] == [None]*3


def test_a_harmful_candidate_is_regressed_with_the_investigation_artifact_and_adoption_blocked(tmp_path):
    stub = fb.StubRuntime(tmp_path)
    base, harm = stub.snapshot('base', range(6)), stub.snapshot('harm', [0, 1])
    result, _, evaluator, _, order = play(tmp_path, stub, base, [harm], regression_margin=0.1, accept_score=0.9)
    assert (result['outcome'], result['stop'], result['best']['snapshot']) == ('regressed', 'regression', base) and order.count('investigate') == 1
    run = evaluator.runs[1]
    assert (run['guard']['verdict'], run['guard']['adoption_blocked']) == ('candidate_regressed', True)
    artifact = json.loads(Path(run['investigation']['path']).read_text())
    assert artifact['profile'] == 'ahl-regression-investigation-v1' and artifact['cause']['class'] == 'candidate_caused'
    assert sorted(item['task'] for item in artifact['flipped']['to_fail']) == ['t2', 't3', 't4', 't5'] and artifact['flipped']['to_pass'] == []
    assert (result['recommendation_only'], result['automatic_promotion']) == (True, False)


def change_grader(stub):
    grader = stub.manifest.parent/'grade.py'
    grader.write_text(grader.read_text() + '# a looser grader\n')


def change_bank(stub):
    (stub.manifest.parent/'fixtures/t3/seed').write_text('3 ')


def change_runtime(stub):
    other = stub.binary.parent/'other'/'claude'
    other.parent.mkdir()
    shutil.copy2(stub.binary, other)
    stub.runner.binary = str(other)


def change_runtime_in_place(stub):
    stub.binary.write_bytes(stub.binary.read_bytes() + b'\n# synthetic runtime revision\n')


@pytest.mark.parametrize('change, field', [(change_grader, 'grader_sha256'), (change_bank, 'bank_sha256'),
                                         (change_runtime, 'runtime_sha256'), (change_runtime_in_place, 'runtime_sha256')],
                         ids=['grader', 'bank', 'runtime', 'runtime-in-place'])
def test_a_change_between_the_evaluations_ends_comparison_invalid_with_no_verdict_and_names_the_pipeline_defect(tmp_path, change, field):
    stub = fb.StubRuntime(tmp_path)
    base, good = stub.snapshot('base', [0, 1]), stub.snapshot('good', range(8))
    result, ledger, evaluator, _, _ = play(tmp_path, stub, base, [good], on_propose=lambda: change(stub), accept_score=0.3)
    assert (result['stop'], result['outcome'], result['best']['snapshot']) == ('comparison_invalid', 'inconclusive', base)
    assert values(ledger, 'candidate_evaluated') == ['comparison_invalid'] and 'decided' not in kinds(ledger)
    spent = evaluator.runs[1]['evaluation']
    assert (spent.trials, spent.evidence) == (0, 'blocked') and not (tmp_path/'work/evaluation-1').exists()  # no trials were spent on an invalid comparison
    artifact = json.loads(Path(evaluator.runs[1]['investigation']['path']).read_text())
    assert (artifact['cause']['class'], artifact['comparison']['mismatched']) == ('pipeline_caused', [field])
    assert 'regression test' in artifact['next_step']


def test_inputs_that_changed_before_the_baseline_ran_end_comparison_invalid_before_any_improvement(tmp_path):
    stub = fb.StubRuntime(tmp_path)
    base, good = stub.snapshot('base', [0, 1]), stub.snapshot('good', range(8))
    spec = fb.declare()
    evaluator = stub.evaluator(spec)
    change_grader(stub)
    proposer = fb.Proposer(base, [good], evaluator)
    result, ledger = fb.drive(tmp_path, evaluator, proposer, spec)
    assert (result['stop'], proposer.calls, kinds(ledger)) == ('comparison_invalid', [], ['spec_frozen', 'baseline_evaluated', 'stopped'])
    assert not (tmp_path/'work/evaluation-0').exists()


def defect_improve_before_the_baseline(loop):
    loop.record('round_started', round=1, mechanism='m1', base=loop.best)


def defect_a_decision_without_a_post_improvement_evaluation(loop):
    loop.record('baseline_evaluated', 'valid', snapshot=loop.best)
    loop.record('round_started', round=1, mechanism='m1', base=loop.best)
    loop.record('decided', 'improved', round=1)


def defect_a_stop_without_a_post_improvement_evaluation(loop):
    loop.record('baseline_evaluated', 'valid', snapshot=loop.best)
    loop.record('round_started', round=1, mechanism='m1', base=loop.best)
    loop.record('stopped', 'acceptance_score_reached')


@pytest.mark.parametrize('defect, written', [
    (defect_improve_before_the_baseline, ['spec_frozen']),
    (defect_a_decision_without_a_post_improvement_evaluation, ['spec_frozen', 'baseline_evaluated', 'round_started']),
    (defect_a_stop_without_a_post_improvement_evaluation, ['spec_frozen', 'baseline_evaluated', 'round_started']),
], ids=['improve-before-baseline', 'decision-without-evaluation', 'stop-without-evaluation'])
def test_an_injected_controller_defect_raises_loop_order_violation_and_writes_no_illegal_event(tmp_path, monkeypatch, defect, written):
    def broken(self):
        self.record('spec_frozen', spec_sha256=self.spec.digest, frozen_inputs_sha256=self.frozen)
        defect(self)
    monkeypatch.setattr(controller._Loop, 'play', broken)
    stub = fb.StubRuntime(tmp_path)
    spec = fb.declare()
    evaluator = stub.evaluator(spec)
    (tmp_path/'project').mkdir()
    ledger = fb.LoopLedger(tmp_path/'store', tmp_path/'project')
    with pytest.raises(LoopError) as error:
        controller.run(spec, evaluator, fb.Proposer(stub.snapshot('base', [0]), []), ledger, lambda: 0.0)
    assert error.value.reason == 'loop_order_violation' and kinds(ledger) == written and evaluator.runs == []
