"""Stage allotments, failure classification and the uninformative decision from synthetic trial records (#739)."""
import argparse
import json

import pytest

from agent_harness_lab import budget_stages as stages, workflow


class Clock:
    def __init__(self): self.value = 0.0
    def __call__(self): return self.value


@pytest.fixture
def clock():
    return Clock()


def trial(task, version, status, *, timeout=False, exit_code=0):
    return dict(task=task, version=version, status=status, split='development', execution=dict(timeout=timeout, exit_code=exit_code))


def arms(status, tasks=('a', 'b'), **facts):
    return [trial(task, version, status, **facts) for task in tasks for version in ('baseline', 'candidate')]


@pytest.mark.parametrize('text', ['0', '1', '-0.1', '1.5', 'nan', 'inf', 'x'])
def test_construction_fraction_must_be_strictly_between_zero_and_one(text):
    with pytest.raises(argparse.ArgumentTypeError):
        stages.fraction(text)
    with pytest.raises(ValueError):
        stages.Stages(100, 0, text)


def test_allotments_always_sum_to_the_whole_budget_and_keep_default_proportions():
    for fraction in (None, 0.1, 0.25, 0.6, 0.9):
        ledger = stages.Stages(200, 0, fraction)
        assert sum(ledger.allotted(name) for name in stages.STAGES) == pytest.approx(200)
    default = stages.Stages(100, 0, clock=clock)
    assert [default.allotted(name) for name in stages.STAGES] == pytest.approx([10, 25, 40, 25])


def test_spend_is_charged_to_stages_and_preparation_is_the_residual(clock):
    ledger = stages.Stages(100, 0, clock=clock)
    clock.value = 7
    with ledger.charge('construction'):
        clock.value = 19
    ledger.add('trials', 4)
    clock.value = 30
    assert ledger.seconds() == dict(preparation=14, refinement=0, construction=12, trials=4)
    ledger.begin_refinement()
    clock.value = 40
    ledger.add('trials', 99)  # a refinement attempt is charged as a whole, not per stage
    assert ledger.seconds()['refinement'] == 10 and ledger.seconds()['trials'] == 4
    receipt = ledger.receipt()
    assert receipt['remaining_seconds'] == 60 and receipt['stages']['refinement'] == dict(allotted_seconds=25.0, spent_seconds=10.0)
    assert json.dumps(receipt)  # seconds and options only


@pytest.mark.parametrize('facts, expected', [
    (dict(status='failed', errors=0), 'agent_failed'),  # a fully read trace with no error record
    (dict(status='failed'), 'unclassified'),  # no trace read in full: tool errors neither seen nor ruled out
    (dict(status='failed', errors=2), 'tool_failure'),
    (dict(status='execution_error', exit_code=3), 'runtime_failure'),
    (dict(status='incomplete'), 'runtime_failure'),
    (dict(status='execution_error', timeout=True, exit_code=None), 'timeout'),
    (dict(status='grader_error'), 'grader_failure'),
])
def test_failure_class_reads_execution_facts_only(facts, expected):
    facts = dict(facts)
    errors = facts.pop('errors', None)
    assert stages.failure_class(trial('a', 'baseline', **facts), errors) == expected


def test_failure_class_ignores_grader_output_text():
    row = trial('a', 'baseline', 'failed')
    row['grader'] = dict(stdout='timeout exit_code=3 tool error', stderr='runtime failure')
    assert stages.failure_class(row, 0) == 'agent_failed' and stages.failure_class(row) == 'unclassified'


def test_tool_errors_count_native_trace_error_records_and_tolerate_noise():
    trace = '\n'.join([
        json.dumps({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'is_error': True}]}}),
        json.dumps({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'is_error': False}]}}),
        json.dumps({'type': 'item.completed', 'item': {'status': 'failed'}}),
        json.dumps({'type': 'turn.failed'}), 'not json', '[1]', json.dumps({'type': 'turn.completed'})])
    assert stages.tool_errors(trace) == 3 and stages.tool_errors('') == 0 and stages.tool_errors(None) is None


def test_every_trial_alike_and_unpassed_is_uninformative_with_one_class_and_one_next_step():
    block = stages.uninformative(arms('failed'), lambda row: json.dumps({'type': 'turn.completed'}))
    assert (block['classification'], block['trials'], block['tasks'], block['class_counts']) == ('agent_failed', 4, 2, {'agent_failed': 4})
    assert block['next_step'] == stages.NEXT_STEP['agent_failed']
    assert stages.uninformative(arms('execution_error', exit_code=1))['classification'] == 'runtime_failure'
    assert stages.uninformative(arms('execution_error', timeout=True, exit_code=None))['classification'] == 'timeout'


def test_a_missing_trace_is_unclassified_never_agent_failed():
    block = stages.uninformative(arms('failed'))
    assert (block['classification'], block['class_counts']) == ('unclassified', {'unclassified': 4})
    assert block['next_step'] == stages.NEXT_STEP['unclassified'] and 'not established' in block['next_step']


def test_tool_errors_in_the_trace_turn_a_failed_run_into_a_tool_failure():
    block = stages.uninformative(arms('failed'), lambda row: json.dumps({'type': 'error'}))
    assert block['classification'] == 'tool_failure'


def test_mixed_causes_are_reported_as_mixed_with_counts():
    rows = [trial('a', 'baseline', 'failed'), trial('a', 'candidate', 'failed'),
            trial('b', 'baseline', 'execution_error', exit_code=2), trial('b', 'candidate', 'execution_error', exit_code=2)]
    block = stages.uninformative(rows, lambda row: '')
    assert block['classification'] == 'mixed' and block['class_counts'] == {'agent_failed': 2, 'runtime_failure': 2}
    assert stages.uninformative(rows)['class_counts'] == {'unclassified': 2, 'runtime_failure': 2}


@pytest.mark.parametrize('rows', [
    [],
    arms('passed'),
    [*arms('failed', ('a',)), *arms('passed', ('b',))],  # one task passes
    [trial('a', 'baseline', 'failed'), trial('a', 'candidate', 'execution_error', exit_code=2)],  # the arms differ
    [trial('a', 'baseline', 'failed')],  # an arm is missing
    [trial('a', 'baseline', 'failed'), trial('a', 'candidate', 'budget_exhausted')],  # budget, not an outcome
    [trial('a', 'baseline', 'not_executed'), trial('a', 'candidate', 'not_executed')],
    [trial('a', 'baseline', 'invalid_input'), trial('a', 'candidate', 'invalid_input')],
    [dict(task='a', version='baseline'), dict(task='a', version='candidate')],  # no status at all: unknown, never a label
])
def test_anything_else_is_not_uninformative(rows):
    assert stages.uninformative(rows) is None


def test_exhausted_names_the_stage_the_counts_and_the_controls_that_did_not_run(clock):
    ledger = stages.Stages(100, 0, clock=clock)
    ledger.add('construction', 40)
    clock.value = 90
    rows = [dict(trial('a', 'baseline', 'failed'), split='development'), dict(trial('a', 'candidate', 'budget_exhausted'), split='development'),
            dict(trial('r', 'baseline', 'passed'), split='regression')]
    block = stages.exhausted(ledger, rows)
    assert (block['consumed_most_by'], block['evidence_label'], block['conclusion']) == ('preparation', 'insufficient', 'not_established')
    assert block['observed'] == dict(trials_recorded=3, baseline_failed=1, baseline_passed=1, trials_unfinished=1)
    assert block['controls_not_run'] == ['adjacent_regression', 'transfer']
    clock.value = 0
    assert stages.exhausted(stages.Stages(100, 0, clock=clock), [])['consumed_most_by'] == 'none'


def test_exhausted_without_reported_trials_says_unknown_not_zero(clock):
    block = stages.exhausted(stages.Stages(100, 0, clock=clock))
    assert (block['observed'], block['controls_not_run'], block['conclusion']) == ('unknown', 'unknown', 'not_established')
    assert stages.exhausted(stages.Stages(100, 0, clock=clock), [])['observed']['trials_recorded'] == 0  # reported and empty is known


def test_a_trial_trace_is_read_whole_or_not_at_all(tmp_path, monkeypatch):
    row = trial('a', 'baseline', 'failed')
    assert workflow._trial_trace(tmp_path, row) is None  # absent, as for offline and controlled trials
    (tmp_path / 'baseline/a').mkdir(parents=True)
    (tmp_path / 'baseline/a/trace.jsonl').write_text('{"type":"error"}\n')
    assert stages.tool_errors(workflow._trial_trace(tmp_path, row)) == 1
    monkeypatch.setattr(stages, 'TRACE_LIMIT', 4)  # a partial read could miss later errors, so an over-bound trace is unread
    assert workflow._trial_trace(tmp_path, row) is None


def test_the_receipt_marks_observations_unknown_unless_the_evaluation_reported_its_trials():
    import types
    def receipt(**attrs):
        result = dict(status='budget_exhausted')
        workflow.construction_receipt(result, types.SimpleNamespace(timeout=100, **attrs))
        return result['budget_exhaustion']
    assert (receipt()['observed'], receipt()['controls_not_run']) == ('unknown', 'unknown')
    assert receipt(_observed=[])['observed']['trials_recorded'] == 0
    assert receipt(_observed=arms('failed'))['observed']['baseline_failed'] == 2
