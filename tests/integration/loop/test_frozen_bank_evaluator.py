"""FrozenBankEvaluator behind the real loop controller: one arm first, fresh paired runs after, a frozen scoring bank, the validity gate (#813).

The stub agent plays the existing trial runner; a scripted runner answers the same port where a test needs a score the stub cannot make.
Every bank, snapshot and outcome is synthetic.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from agent_harness_lab.budget_stages import Stages
from agent_harness_lab.evaluation import integrity, vacuity
from agent_harness_lab.loop import adapters
from agent_harness_lab.loop.controller import classify
from agent_harness_lab.loop.errors import LoopError
from tests.support import frozen_bank as fb

BASE, CAND = fb.sha('base'), fb.sha('candidate')
RADIUS = 0.6106666186999493  # sqrt(2 ln(2 / 0.45) * 8 * (1/8)^2): alpha 0.9 over two looks, eight independent roots
PASS_ALL, FAIL_ALL = fb.outcome_table(range(8)), fb.outcome_table(())
UNKNOWN = {f't{number}': 'execution_error' for number in range(8)}


def kinds(ledger):
    return [kind for kind, _ in ledger.events()]


def values(ledger, kind):
    return [payload['value'] for event, payload in ledger.events() if event == kind]


def test_runtime_identity_reuses_hash_until_the_executable_changes(tmp_path, monkeypatch):
    from agent_harness_lab.loop import cold_resume
    binary = tmp_path / 'agent'
    binary.write_text('first')
    binary.chmod(0o700)
    hashed = []
    real = cold_resume.runtime_sha256
    def capture(path):
        hashed.append(path)
        return real(path)
    monkeypatch.setattr(cold_resume, 'runtime_sha256', capture)
    runner = adapters.ExecutorRunner(None, 'codex', str(binary))
    first = runner.runtime_sha256()
    assert runner.runtime_sha256() == first and len(hashed) == 1
    assert runner.runtime_executable_sha256() == hashlib.sha256(b'first').hexdigest() and len(hashed) == 1
    before = binary.stat()
    binary.write_text('other')
    os.utime(binary, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert runner.runtime_sha256() != first and len(hashed) == 2
    binary.unlink()
    with pytest.raises(ValueError, match='runtime executable unavailable'):
        runner.runtime_sha256()


def test_runtime_identity_refuses_an_executable_changed_while_hashing(tmp_path, monkeypatch):
    from agent_harness_lab.loop import cold_resume
    binary = tmp_path / 'agent'
    binary.write_text('first')
    binary.chmod(0o700)
    real = cold_resume.runtime_sha256
    def change_during_hash(path):
        result = real(path)
        path.write_text('changed')
        return result
    monkeypatch.setattr(cold_resume, 'runtime_sha256', change_during_hash)
    with pytest.raises(ValueError, match='changed while hashing'):
        adapters.ExecutorRunner(None, 'codex', str(binary)).runtime_sha256()


def test_runtime_changed_and_restored_during_evaluation_leaves_inputs_unknown(tmp_path, monkeypatch):
    stub = fb.StubRuntime(tmp_path)
    base, good = stub.snapshot('base', [0, 1]), stub.snapshot('good', range(8))
    evaluator = stub.evaluator(fb.declare())
    evaluator.evaluate(base, evaluator.frozen_inputs)
    real = stub.runner.run
    def change_and_restore(*args, **kwargs):
        original = stub.binary.read_bytes()
        stub.binary.write_bytes(original + b'\n# synthetic runtime revision\n')
        try: return real(*args, **kwargs)
        finally: stub.binary.write_bytes(original)
    monkeypatch.setattr(stub.runner, 'run', change_and_restore)
    evaluator.bases[good] = base
    result = evaluator.evaluate(good, evaluator.frozen_inputs)
    assert result.trials > 0 and result.inputs is None
    assert evaluator.runs[-1]['guard']['frozen_inputs']['comparison']['status'] == 'unverified'


def test_the_baseline_is_scored_from_one_arm_and_a_later_evaluation_is_a_fresh_paired_run(tmp_path):
    stub = fb.StubRuntime(tmp_path)
    base, good = stub.snapshot('base', [0, 1]), stub.snapshot('good', range(8))
    evaluator = stub.evaluator(fb.declare())
    first = evaluator.evaluate(base, evaluator.frozen_inputs)
    one_arm = json.loads((tmp_path/'work/evaluation-0/results.json').read_text())
    assert sorted(one_arm['versions']) == ['baseline'] and {trial['version'] for trial in one_arm['trials']} == {'baseline'} and len(one_arm['trials']) == 8
    assert (first.point, first.lower, first.upper) == (0.25, 0.0, pytest.approx(0.25 + RADIUS)) and (first.trials, first.reserve_trials) == (8, 16)
    kept = hashlib.sha256((tmp_path/'work/evaluation-0/results.json').read_bytes()).hexdigest()
    evaluator.bases[good] = base
    second = evaluator.evaluate(good, evaluator.frozen_inputs)
    paired = json.loads((tmp_path/'work/evaluation-1/results.json').read_text())
    assert sorted(paired['versions']) == ['baseline', 'candidate'] and len(paired['trials']) == 16 and second.trials == 16
    assert (second.point, second.lower, second.upper) == (1.0, pytest.approx(1 - RADIUS), 1.0)
    assert (tmp_path/'work/evaluation-1/baseline/t0/workspace').is_dir()  # the best arm ran again; nothing of evaluation 0 was reused
    assert hashlib.sha256((tmp_path/'work/evaluation-0/results.json').read_bytes()).hexdigest() == kept


def test_trials_the_agent_crashes_on_are_unknown_outcomes_and_the_evaluation_is_vacuous(tmp_path):
    stub = fb.StubRuntime(tmp_path)
    crash = stub.snapshot('crash', text='crash\n')
    evaluator = stub.evaluator(fb.declare())
    found = evaluator.evaluate(crash, evaluator.frozen_inputs)
    assert (found.vacuity, found.evidence, evaluator.runs[0]['labels'], classify(found)) == (
        'all_outcomes_unknown', 'no_evidence', ['all_outcomes_unknown', 'grader_not_executed'], 'vacuous')


def test_held_out_tasks_are_never_run_hashed_or_scored(tmp_path, monkeypatch):
    opened = []
    real = integrity.digest
    monkeypatch.setattr(integrity, 'digest', lambda root, **limits: opened.append(str(root)) or real(root, **limits))
    stub = fb.StubRuntime(tmp_path, heldout=3)
    base = stub.snapshot('base', [0, 1])
    evaluator = stub.evaluator(fb.declare())
    evaluator.evaluate(base, evaluator.frozen_inputs)
    results = json.loads((tmp_path/'work/evaluation-0/results.json').read_text())
    scoring = json.loads((tmp_path/'bank/loop-scoring-manifest.json').read_text())
    assert {trial['task'] for trial in results['trials']} == {f't{number}' for number in range(8)} == {task['id'] for task in scoring['tasks']}
    assert evaluator.excluded == 3 and not any(Path(path).name.startswith('h') for path in opened) and any(Path(path).name == 't0' for path in opened)
    assert not any((tmp_path/'bank/fixtures'/f'h{number}').exists() for number in range(3))


def test_a_bank_that_already_declares_certification_access_is_refused_before_anything_runs(tmp_path):
    manifest = fb.write_bank(tmp_path)
    loaded = json.loads(manifest.read_text())
    manifest.write_text(json.dumps(dict(loaded, protected_access=dict(authority='declared'))))
    with pytest.raises(LoopError) as error:
        adapters.FrozenBankEvaluator(manifest, fb.ScriptedRunner(), lambda name: name, tmp_path/'work', spec=fb.declare())
    assert (error.value.reason, error.value.field) == ('loop_port_invalid', 'bank') and not (tmp_path/'bank/loop-scoring-manifest.json').exists()


BASELINES = {
    'no_eligible_cases': (dict(scoring=0, heldout=2), {}, ()),
    'zero_trials': ({}, {}, ()),
    'all_outcomes_unknown': ({}, {BASE: UNKNOWN}, ()),
    'grader_not_executed': ({}, {BASE: PASS_ALL}, (BASE,)),
}


@pytest.mark.parametrize('label', list(BASELINES))
def test_a_vacuous_baseline_stops_the_loop_before_any_improvement_and_spends_only_the_baseline_evaluation(tmp_path, label):
    options, outcomes, ungraded = BASELINES[label]
    evaluator, runner = fb.scripted(tmp_path, outcomes, ungraded=ungraded, accept_score=0.3, **options)
    proposer = fb.Proposer(BASE, [CAND], evaluator)
    result, ledger = fb.drive(tmp_path, evaluator, proposer, evaluator.spec)
    assert (result['outcome'], result['stop'], result['best']['snapshot']) == ('inconclusive', 'evidence_insufficient', BASE)
    assert proposer.calls == [] and kinds(ledger) == ['spec_frozen', 'baseline_evaluated', 'stopped'] and values(ledger, 'baseline_evaluated') == ['vacuous']
    assert runner.calls == ([] if label == 'no_eligible_cases' else [('baseline',)]) and result['usage']['trials'] == (8 if label in ('all_outcomes_unknown', 'grader_not_executed') else 0)


CANDIDATES = {
    'zero_trials': ({BASE: fb.outcome_table({0, 1})}, (), CAND),
    'all_outcomes_unknown': ({BASE: fb.outcome_table({0, 1}), CAND: UNKNOWN}, (), CAND),
    'grader_not_executed': ({BASE: fb.outcome_table({0, 1}), CAND: PASS_ALL}, (CAND,), CAND),
    'arms_identical': ({BASE: fb.outcome_table({0, 1})}, (), BASE),
}


@pytest.mark.parametrize('label', list(CANDIDATES))
def test_a_vacuous_candidate_is_inconclusive_is_never_the_best_and_never_accepted(tmp_path, label):
    outcomes, ungraded, staged = CANDIDATES[label]
    evaluator, _ = fb.scripted(tmp_path, outcomes, ungraded=ungraded, accept_score=0.3)
    result, ledger = fb.drive(tmp_path, evaluator, fb.Proposer(BASE, [staged], evaluator), evaluator.spec)
    assert (result['outcome'], result['stop'], result['best']['snapshot']) == ('inconclusive', 'evidence_insufficient', BASE)
    assert values(ledger, 'candidate_evaluated') == ['vacuous'] and values(ledger, 'decided') == ['inconclusive'] and evaluator.runs[1]['labels'][0] == label


def stop_of(base):
    evaluator, _ = fb.scripted(base, {BASE: PASS_ALL}, ungraded=(BASE,), accept_score=0.3)
    return fb.drive(base, evaluator, fb.Proposer(BASE, [CAND], evaluator), evaluator.spec)[0]['stop']


def test_without_the_label_a_run_with_no_grader_execution_would_be_accepted_so_the_gate_is_what_stops_it(tmp_path, monkeypatch):
    assert stop_of(tmp_path/'labelled') == 'evidence_insufficient'
    monkeypatch.setitem(vacuity.RULES, 'grader_not_executed', lambda *args: False)
    assert stop_of(tmp_path/'unlabelled') == 'accepted_without_change'


def test_fewer_independent_roots_than_the_plan_minimum_stop_the_loop_and_cannot_be_accepted(tmp_path):
    evaluator, _ = fb.scripted(tmp_path, {BASE: fb.outcome_table({0}, scoring=1)}, scoring=1, accept_score=0.1)
    proposer = fb.Proposer(BASE, [CAND], evaluator)
    result, ledger = fb.drive(tmp_path, evaluator, proposer, evaluator.spec)
    assert (result['outcome'], result['stop']) == ('inconclusive', 'evidence_insufficient') and proposer.calls == [] and values(ledger, 'baseline_evaluated') == ['incomplete']


def test_an_unscored_root_keeps_the_baseline_from_being_accepted_even_when_the_scored_ones_pass(tmp_path):
    table = {**PASS_ALL, 't7': 'budget_exhausted'}
    evaluator, _ = fb.scripted(tmp_path, {BASE: table}, accept_score=0.1)
    result, _ = fb.drive(tmp_path, evaluator, fb.Proposer(BASE, [CAND], evaluator), evaluator.spec)
    assert (result['outcome'], result['stop']) == ('inconclusive', 'evidence_insufficient')


@pytest.mark.parametrize('trials, started', [(20, False), (24, True)], ids=['too-little-left-for-a-paired-run', 'exactly-enough'])
def test_a_round_starts_only_when_what_is_left_pays_for_two_arms_and_spend_never_passes_the_limit(tmp_path, trials, started):
    # The baseline spends 8 trials (one arm) and reserves 16 (a paired re-evaluation): 20 - 8 = 12 < 16, but 24 - 8 = 16 is enough.
    evaluator, _ = fb.scripted(tmp_path, {BASE: fb.outcome_table({0, 1}), CAND: PASS_ALL}, trials=trials, accept_score=0.3)
    proposer = fb.Proposer(BASE, [CAND], evaluator)
    result, _ = fb.drive(tmp_path, evaluator, proposer, evaluator.spec)
    assert (len(proposer.calls) == 1, result['stop']) == (started, 'acceptance_score_reached' if started else 'budget_exhausted')
    assert result['usage']['trials'] <= trials


def test_first_use_runs_construction_once_and_freezes_the_bank_for_every_round(tmp_path):
    constructed = []
    ticks = iter([0.0, 5.0])

    def construct():
        constructed.append(1)
        return fb.write_bank(tmp_path, 8, 0)

    stages = Stages(100, 0.0, clock=lambda: next(ticks))
    tie = fb.outcome_table({0, 1})
    runner = fb.ScriptedRunner({BASE: tie, CAND: tie, fb.sha('c2'): tie, fb.sha('c3'): tie})
    evaluator = adapters.FrozenBankEvaluator.first_use(construct, runner, lambda name: name, tmp_path/'work', spec=fb.declare(max_rounds=3, plateau_rounds=3), stages=stages)
    frozen = evaluator.frozen_inputs
    result, ledger = fb.drive(tmp_path, evaluator, fb.Proposer(BASE, [CAND, fb.sha('c2'), fb.sha('c3')], evaluator), evaluator.spec)
    assert (len(constructed), evaluator.constructions, stages.spent['construction']) == (1, 1, 5.0)
    assert {run['identity']['identity_sha256'] for run in evaluator.runs} == {frozen} and len(evaluator.runs) == 1 + result['rounds']
    assert result['frozen_inputs_sha256'] == frozen and [payload['evaluation']['inputs'] for kind, payload in ledger.events() if kind.endswith('_evaluated')] == [frozen]*(1 + result['rounds'])


@pytest.mark.parametrize('arms', [('baseline', 'baseline'), ('other',), ('candidate', 'baseline', 'candidate')], ids=['repeated', 'unknown', 'three'])
def test_the_trial_runner_refuses_arms_it_does_not_know_before_running_anything(tmp_path, arms):
    from argparse import Namespace
    stub = fb.StubRuntime(tmp_path)
    scoring, _ = adapters.scoring_manifest(stub.manifest)
    base = stub.snapshots(stub.snapshot('base', [0]))
    options = Namespace(manifest=str(scoring), baseline=str(base), candidate=str(base), output=str(tmp_path/'out'), agent='claude',
                        codex=str(stub.binary), claude=str(stub.binary), arms=arms)
    with pytest.raises(ValueError, match='arms must name'):
        stub.runner.evaluate(options)
    assert not (tmp_path/'out').exists()


def test_a_repeated_plan_cannot_run_one_arm(tmp_path):
    from argparse import Namespace
    stub = fb.StubRuntime(tmp_path)
    scoring, _ = adapters.scoring_manifest(stub.manifest)
    loaded = json.loads(scoring.read_text())
    scoring.write_text(json.dumps(dict(loaded, experiment=dict(profile='descriptive_smoke'))))
    base = stub.snapshots(stub.snapshot('base', [0]))
    options = Namespace(manifest=str(scoring), baseline=str(base), output=str(tmp_path/'out'), agent='claude', codex=str(stub.binary), claude=str(stub.binary), arms=('baseline',))
    with pytest.raises(ValueError, match='repeated plan needs both'):
        stub.runner.evaluate(options)
    assert not (tmp_path/'out').exists()
