"""The fixed repeated-bank lane records the before/after guard and blocks a worse candidate (#804).

Synthetic offline runs and scripted stores only; the model, the kernel profile and every real project stay out of it.
"""
import json
import time
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import before_after
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from agent_harness_lab.evaluation.repeated_workflow import Request
from agent_harness_lab.evaluation.statistics import TrialRecord
from tests.integration.evaluation.test_offline_evaluation import SOLVER
from tests.integration.evaluation.test_oracle_history import OracleHistoryTests

FAILS = "from pathlib import Path; Path('result.json').write_text('{}')\n"
NOT_SUPPORTED = dict(status='inconclusive', reason='certification_prerequisites_unverified', recommendation='candidate_not_supported',
                     controller_run_id='run', publication_allowed=False, automatic_promotion=False)


class Comparison(OracleHistoryTests):
    """A sealed two-task offline comparison; ``baseline`` and ``candidate`` are the solvers each arm runs."""

    def __init__(self, add_cleanup, baseline=FAILS, candidate=SOLVER):
        super().__init__(add_cleanup)
        (self.root / 'baseline' / 'solve.py').write_text(baseline)
        (self.root / 'candidate' / 'solve.py').write_text(candidate)
        self.install_plan(self.all_tasks)  # the frozen plan digests both folders, so it is rebuilt after the swap
        self.sealed, _ = self.run_history()

    def guarded(self, result=None, run_id=None, output=None):
        result = dict(NOT_SUPPORTED if result is None else result)
        output = self.root / 'guard-output' if output is None else output
        output.mkdir(exist_ok=True)
        before_after.attach_experiment(result, output, self.store, run_id or self.sealed['run_id'], bank_sha256='a' * 64, tasks=self.manifest['tasks'])
        return result, output


@pytest.fixture
def comparison():
    with ExitStack() as cleanups:
        yield lambda **solvers: Comparison(cleanups.callback, **solvers)


def test_a_sealed_comparison_whose_candidate_is_worse_is_blocked_and_investigated(comparison):
    case = comparison(baseline=SOLVER, candidate=FAILS)
    result, output = case.guarded(dict(NOT_SUPPORTED, recommendation='review_candidate'))
    assert (result['status'], result['reason']) == ('blocked', 'candidate_regressed')
    assert 'recommendation' not in result
    guard = result['before_after']
    assert (guard['verdict'], guard['adoption_blocked'], guard['margin'], guard['gain_margin']) == ('candidate_regressed', True, 0.1, 0.05)
    assert guard['delta']['mean'] == -1 and guard['pairs']['flipped_to_fail'] == 2 and guard['bindings']['controller_run_id'] == case.sealed['run_id']
    assert guard['frozen_inputs']['comparison'] == dict(status='identical', mismatched=[], unknown=[])
    artifact = json.loads((output / 'investigation.json').read_text())
    assert result['investigation']['cause'] == artifact['cause']['class'] == 'candidate_caused'
    assert len(artifact['flipped']['to_fail']) == 2 and artifact['flipped']['to_pass'] == []


def test_a_sealed_comparison_whose_candidate_is_better_is_not_blocked_and_keeps_its_recommendation(comparison):
    case = comparison()
    result, output = case.guarded(dict(NOT_SUPPORTED, recommendation='review_candidate'))
    assert (result['status'], result['recommendation']) == ('inconclusive', 'review_candidate')
    guard = result['before_after']
    assert guard['verdict'] in ('improved', 'inconclusive') and guard['adoption_blocked'] is False and guard['delta']['mean'] == 1
    assert not (output / 'investigation.json').exists() and 'investigation' not in result


def test_a_sealed_comparison_that_ties_is_inconclusive_and_never_improved(comparison):
    case = comparison(baseline=SOLVER, candidate=SOLVER)
    result, output = case.guarded()
    assert (result['status'], result['recommendation']) == ('inconclusive', 'candidate_not_supported')
    assert (result['before_after']['verdict'], result['before_after']['delta']['mean']) == ('inconclusive', 0)
    assert not (output / 'investigation.json').exists()


def test_a_run_the_store_does_not_know_leaves_the_result_unchanged_and_the_block_unavailable(comparison):
    case = comparison()
    result, output = case.guarded(run_id='f' * 64)
    assert (result['status'], result['recommendation']) == ('inconclusive', 'candidate_not_supported')
    assert result['before_after']['status'] == 'unavailable' and result['before_after']['adoption_authority'] is False
    assert not (output / 'investigation.json').exists()


class Scripted:
    """A store that only answers what ``Request.execute`` asks, with the sealed trials a test scripts."""

    def __init__(self, spec, records, analysis):
        self.spec, self.records, self.analysis = spec, records, analysis

    def __call__(self, path):
        return self

    def run(self, *args, **kwargs):
        return dict(run_id='run', state='sealed')

    def decision(self, run_id):
        return dict(receipt_sha256='r' * 64)

    def verify_decision(self, run_id, receipt_sha256):
        return dict(analysis=self.analysis, receipt_sha256=receipt_sha256)

    def sealed_trials(self, run_id):
        if isinstance(self.records, Exception): raise self.records
        return self.spec, self.records


def synthetic_request(tmp_path):
    request = Request.__new__(Request)
    request.store, request.output, request.deadline, request.permission_profile = tmp_path / 'store', tmp_path / 'output', time.monotonic() + 60, None
    request.request_hash = 'a' * 64
    request.value = dict(bank_sha256='b' * 64, cache_policy='fresh_controller_calls_provider_cache_unknown')
    request.source_value = dict(tasks=[dict(id='t0', split='development'), dict(id='t1', split='regression')])
    request.check = lambda: 30
    request.output.mkdir()
    return request


def synthetic_trials(favour):
    spec = ExperimentSpec('synthetic', (('t0', 'r0', 'fam'), ('t1', 'r1', 'fam')), 2, 7, 8, 2, 0.01, 0.02, 0.05, (('fam', 1.0),), 'b' * 64, 'c' * 64, 'd' * 64, 'e' * 64)
    return spec, [TrialRecord(row['trial_id'], (row['version'] == 'baseline') == (favour == 'baseline'), 'graded', 'executable_grader', spec.baseline_hash,
                              spec.candidate_hash, spec.grader_hash, spec.runtime_hash) for row in schedule(spec)]


def executed(tmp_path, spec, records, analysis):
    request = synthetic_request(tmp_path)
    with patch('agent_harness_lab.evaluation.oracle_history.HistoryStore', Scripted(spec, records, analysis)):
        return request, request.execute(tmp_path / 'output' / 'manifest.json', tmp_path / 'baseline', tmp_path / 'candidate',
                                        type('Args', (), dict(cgroup_root=None, image_root=None, image_sha256=None, disk_bytes=None, inference_settings=None))(),
                                        None, None)


def test_the_fixed_bank_run_ends_blocked_with_the_typed_reason_when_the_candidate_is_worse(tmp_path):
    spec, records = synthetic_trials('baseline')
    request, result = executed(tmp_path, spec, records, dict(status='rejected', reason='correctness_regression'))
    assert (result['status'], result['reason']) == ('blocked', 'candidate_regressed')
    assert 'recommendation' not in result and result['analysis']['status'] == 'rejected'
    assert result['before_after']['verdict'] == 'candidate_regressed' and (request.output / 'investigation.json').is_file()
    assert result['publication_allowed'] is False and result['automatic_promotion'] is False


def test_the_fixed_bank_run_keeps_its_recommendation_when_the_candidate_is_better(tmp_path):
    spec, records = synthetic_trials('candidate')
    request, result = executed(tmp_path, spec, records, dict(status='supported'))
    assert (result['status'], result['reason'], result['recommendation']) == ('inconclusive', 'certification_prerequisites_unverified', 'review_candidate')
    assert result['before_after']['adoption_blocked'] is False and not (request.output / 'investigation.json').exists()


def test_a_guard_that_cannot_read_the_trials_never_changes_the_fixed_bank_result(tmp_path):
    spec, _ = synthetic_trials('baseline')
    request, result = executed(tmp_path, spec, ValueError('synthetic'), dict(status='rejected', reason='correctness_regression'))
    assert (result['status'], result['recommendation']) == ('inconclusive', 'candidate_not_supported')
    assert result['before_after'] == dict(status='unavailable', reason='ValueError', adoption_authority=False)
    assert not (request.output / 'investigation.json').exists()
