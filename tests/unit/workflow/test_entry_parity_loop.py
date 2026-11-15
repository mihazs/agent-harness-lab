"""The loop receipt is part of the CLI/skill parity contract: its timing keys may differ in value, every other key must match (#814).

The receipt is the real builder's output over scripted ports, so a key the builder adds is compared the way it is written.
"""
import copy

import pytest

from agent_harness_lab import entry_parity
from agent_harness_lab.loop import receipt
from tests.support import frozen_bank as fb

BASE, CAND = fb.sha('base'), fb.sha('candidate')
RUN_A, RUN_B = '/state/runs/a/run-1', '/state/runs/b/run-2'


@pytest.fixture
def loop(tmp_path):
    evaluator, _ = fb.scripted(tmp_path, {BASE: fb.outcome_table({0, 1}), CAND: fb.outcome_table(range(8))}, accept_score=0.3)
    result, ledger = fb.drive(tmp_path, evaluator, fb.Proposer(BASE, [CAND], evaluator), evaluator.spec)
    stages = dict(profile='ahl-budget-stages-v1', total_seconds=60, remaining_seconds=50.5, construction_fraction=0.25,
                  stages={name: dict(allotted_seconds=1.0, spent_seconds=0.5) for name in ('preparation', 'construction', 'trials', 'refinement')})
    return receipt.build(evaluator.spec, result, ledger.events(), [run['guard'] for run in evaluator.runs], budget=stages)


def workflow(loop, run, **extra):
    return dict(status='accepted', output=run, loop=loop, **extra)


def differences(first, second):
    return entry_parity.document_differences(entry_parity.RECEIPT, first, second, RUN_A, RUN_B)


def retimed(loop, seconds):
    loop = copy.deepcopy(loop)
    loop['usage']['seconds'] = seconds
    loop['budget_stages']['remaining_seconds'] = 60 - seconds
    for stage in loop['budget_stages']['stages'].values(): stage['spent_seconds'] = seconds / 4
    return loop


def test_two_runs_of_one_receipt_that_differ_only_in_their_measured_time_agree(loop):
    assert differences(workflow(loop, RUN_A), workflow(retimed(loop, 3.2), RUN_B)) == []


@pytest.mark.parametrize('key', ['loop.usage.seconds', 'loop.budget_stages.remaining_seconds', 'loop.budget_stages.stages.trials.spent_seconds'])
def test_each_listed_timing_key_is_allowed_to_differ_in_value(key):
    assert entry_parity.allowed(entry_parity.RECEIPT, key)


@pytest.mark.parametrize('mutate, key', [
    pytest.param(lambda l: l.update(stop_criterion='plateau'), 'loop.stop_criterion', id='the-stop-criterion'),
    pytest.param(lambda l: l['rounds'][0].update(decision='regressed'), 'loop.rounds.0.decision', id='a-round-decision'),
    pytest.param(lambda l: l['rounds'][0]['score'].update(point=0.5), 'loop.rounds.0.score.point', id='a-score'),
    pytest.param(lambda l: l['before'].update(evaluation_id='evaluation-9'), 'loop.before.evaluation_id', id='an-evaluation-id'),
    pytest.param(lambda l: l.update(frozen_inputs_sha256='0'*64), 'loop.frozen_inputs_sha256', id='the-frozen-inputs'),
    pytest.param(lambda l: l['spec'].update(digest='0'*64), 'loop.spec.digest', id='the-spec-digest'),
    pytest.param(lambda l: l['spec']['limits'].update(seconds=1), 'loop.spec.limits.seconds', id='a-declared-limit'),
    pytest.param(lambda l: l['spec']['sources'].update(accept_score='project'), 'loop.spec.sources.accept_score', id='the-source-of-a-value'),
    pytest.param(lambda l: l.update(certification='verified'), 'loop.certification', id='certification'),
    pytest.param(lambda l: l['usage'].update(trials=99), 'loop.usage.trials', id='the-trials-spent'),
    pytest.param(lambda l: l['rounds'].pop(), 'loop.rounds.0.decision', id='a-missing-round'),
])
def test_any_other_loop_difference_is_reported_with_its_dotted_key(loop, mutate, key):
    other = copy.deepcopy(loop)
    mutate(other)
    assert key in differences(workflow(loop, RUN_A), workflow(other, RUN_B))


def test_a_timing_key_missing_from_one_route_is_reported_because_a_duration_may_differ_in_value_never_in_presence(loop):
    other = copy.deepcopy(loop)
    del other['usage']['seconds']
    assert differences(workflow(loop, RUN_A), workflow(other, RUN_B)) == ['loop.usage.seconds']


def test_a_loop_block_present_on_one_route_only_is_reported_key_by_key(loop):
    assert 'loop.stop_criterion' in differences(workflow(loop, RUN_A), dict(status='accepted', output=RUN_B))


def test_the_run_directory_inside_a_loop_value_is_normalized_not_compared(loop):
    first, second = workflow(loop, RUN_A, recommended_candidate=dict(path=RUN_A + '/candidate-0')), workflow(loop, RUN_B, recommended_candidate=dict(path=RUN_B + '/candidate-0'))
    assert differences(first, second) == []


def test_every_key_the_receipt_builder_writes_is_either_compared_or_a_listed_timing_key(loop):
    flat = {key for key, _ in entry_parity._flatten(dict(loop=loop))}
    timing = {key for key in flat if entry_parity.allowed(entry_parity.RECEIPT, key)}
    assert timing == {'loop.usage.seconds', *{key for key in flat if key.startswith('loop.budget_stages.') and key.endswith(('remaining_seconds', 'spent_seconds'))}}
