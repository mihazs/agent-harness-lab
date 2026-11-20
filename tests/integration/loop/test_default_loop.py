"""`ahl improve` runs the evaluate > improve > evaluate loop by default (#817); `--single-pass` keeps the previous pass for one release.

Everything is the real entry point over a synthetic project, a pinned copy of the starter bank and a scripted stub agent
(tests/support/loop_improve.py). With five scored roots only a high alpha lets an interval clear a bar, so the runs that need a
decided score declare alpha in the project's `loop` object, which is a declaration of that one field and never of the bar.
"""
from contextlib import ExitStack
import json
from pathlib import Path

import pytest

from agent_harness_lab import entry_parity
from agent_harness_lab.loop import entry as loop_entry, improve as loop_improve, machine
from tests.support import loop_improve as li

NOT_USED_KEYS = {'profile', 'status', 'reason', 'single_pass', 'deprecated', 'recommendation_only', 'automatic_promotion', 'certification', 'remediation'}


@pytest.fixture
def stack():
    with ExitStack() as stack:
        yield stack


@pytest.fixture(autouse=True)
def three_mechanisms(monkeypatch):
    monkeypatch.setattr(loop_improve, 'mechanisms', lambda args: ['m1', 'm2', 'm3'])


def improving(stack, monkeypatch, **extra):
    """A pinned-bank project whose first preparation call proposes a harness that solves every scored task."""
    return li.LoopProject(stack, monkeypatch, script={0: dict(harness=li.EVERYTHING)}, **extra)


@pytest.mark.parametrize('entry', [pytest.param(('improve',), id='improve'), pytest.param(('skills', 'invoke'), id='skills-invoke')])
def test_with_no_loop_flag_a_pinned_bank_runs_the_loop_with_the_labelled_defaults(stack, monkeypatch, entry):
    project = improving(stack, monkeypatch)
    code, found, _ = project.invoke(entry=entry)
    loop = found['loop']
    assert loop['stop_criterion'] in machine.STOPS and found['reason'] == f'loop_{loop["stop_criterion"]}'
    assert (loop['acceptance_source'], loop['mode'], loop['certification']) == ('default', 'relative_supported_improvement', 'unverified')
    assert (loop['recommendation_only'], loop['automatic_promotion'], found['changed_files']) == (True, False, [])
    assert {name: loop['spec']['sources'][name] for name in ('accept_score', 'max_rounds', 'plateau_rounds', 'on_regression')} == dict(accept_score='default', max_rounds='default', plateau_rounds='default', on_regression='default')
    assert (loop['spec']['max_rounds'], loop['spec']['accept_score']) == (3, None)


def test_the_default_loop_scores_the_baseline_before_it_asks_for_any_improvement(stack, monkeypatch):
    project = improving(stack, monkeypatch)
    _, found, _ = project.invoke()
    loop, run = found['loop'], Path(found['output'])
    assert loop['before']['evaluation_id'] == 'evaluation-0' and loop['before']['score'] is not None
    assert [round_['evaluation_id'] for round_ in loop['rounds']][:1] == ['evaluation-1'] and project.calls()[0]['mechanism'] == 'm1'
    baseline = json.loads((run/'evaluation-0/results.json').read_text())
    assert {trial['version'] for trial in baseline['trials']} == {'baseline'}  # the first evaluation ran before any candidate existed


def test_a_baseline_that_already_meets_a_declared_bar_stops_without_improving_in_the_default_loop(stack, monkeypatch):
    project = li.LoopProject(stack, monkeypatch, baseline=li.EVERYTHING, loop=dict(accept_score=0.1, alpha=0.999))
    code, found, _ = project.invoke()
    assert (code, found['loop']['stop_criterion'], project.calls()) == (0, 'accepted_without_change', [])


def test_max_rounds_one_is_exactly_one_evaluate_improve_evaluate_pass(stack, monkeypatch):
    project = improving(stack, monkeypatch)
    _, found, _ = project.invoke('--max-rounds', '1')
    loop = found['loop']
    assert len(loop['rounds']) <= 1 and loop['spec']['max_rounds'] == 1 and len(project.calls()) <= 1
    assert (loop['before']['evaluation_id'], loop['spec']['sources']['max_rounds']) == ('evaluation-0', 'cli')


@pytest.mark.parametrize('entry', [pytest.param(('improve',), id='improve'), pytest.param(('skills', 'invoke'), id='skills-invoke')])
def test_single_pass_reproduces_the_previous_behavior_and_says_the_loop_was_not_used(stack, monkeypatch, entry):
    baseline = 'solves=responses'  # the single pass recommends only when the baseline holds its calibration control and fails a development task
    project = li.LoopProject(stack, monkeypatch, baseline=baseline, script={0: dict(harness=li.EVERYTHING)})
    code, found, _ = project.invoke('--single-pass', entry=entry)
    assert (code, found['status'], found['recommendation']) == (3, 'inconclusive', 'candidate_requires_independent_certification')
    assert set(found['loop']) == NOT_USED_KEYS and (found['loop']['status'], found['loop']['reason'], found['loop']['deprecated']) == ('not_used', 'single_pass_requested', True)
    assert found['loop']['remediation'] == loop_entry.NOT_USED['single_pass_requested'] and 'stop_criterion' not in found['loop']
    assert (project.project/'AGENTS.md').read_text() == baseline + '\n'


@pytest.mark.parametrize('flags', [['--accept-score', '0.5'], ['--max-rounds', '2'], ['--plateau-rounds', '1'], ['--on-regression', 'stop']],
                         ids=['accept-score', 'max-rounds', 'plateau-rounds', 'on-regression'])
def test_single_pass_does_not_combine_with_a_loop_flag_and_nothing_runs(stack, monkeypatch, flags):
    project = improving(stack, monkeypatch)
    code, found, _ = project.invoke('--single-pass', *flags)
    assert (code, found['status'], found['reason']) == (2, 'invalid_input', 'invalid_usage') and project.calls() == []


def test_single_pass_wins_over_a_project_loop_declaration_without_reading_it(stack, monkeypatch):
    project = li.LoopProject(stack, monkeypatch, baseline='solves=responses', script={0: dict(harness=li.EVERYTHING)}, loop=dict(accept_score=0.5))
    code, found, _ = project.invoke('--single-pass')
    assert found['loop']['status'] == 'not_used' and found['loop']['reason'] == 'single_pass_requested' and code == 3


@pytest.mark.parametrize('entry', [pytest.param(('improve',), id='improve'), pytest.param(('skills', 'invoke'), id='skills-invoke')])
def test_a_default_run_without_a_pinned_bank_constructs_an_evaluation_first_and_never_makes_a_single_pass(stack, monkeypatch, entry):
    project = li.LoopProject(stack, monkeypatch, script={0: dict(harness=li.EVERYTHING)}, pinned=False)
    code, found, _ = project.invoke(entry=entry)
    label = found['loop']  # fixed-in: 0.232.0; before, this run fell back to an improve-first single pass labelled not_used
    assert (found['status'], found['reason'], label['status']) == ('execution_error', 'execution_failed', 'not_started') and project.calls() == [dict(phase='evaluation')]
    assert label.get('reason') != 'loop_requires_frozen_bank' and 'single_pass' not in label
    assert json.loads((Path(found['output'])/'workflow.json').read_text())['loop'] == label


def test_an_explicit_declaration_without_a_pinned_bank_constructs_an_evaluation_first(stack, monkeypatch):
    project = improving(stack, monkeypatch, pinned=False)
    code, found, _ = project.invoke('--max-rounds', '2')
    assert (found['status'], found['reason'], found['loop']['status']) == ('execution_error', 'execution_failed', 'not_started') and project.calls() == [dict(phase='evaluation')]


def test_a_repair_contracts_run_is_no_improvement_pass_and_carries_no_loop_label(stack, monkeypatch):
    project = improving(stack, monkeypatch)
    code, found, _ = project.invoke('--repair-contracts', '--scope', 'project')
    assert 'loop' not in found and project.calls() == []


def test_the_entry_parity_compares_the_label_of_both_entries_as_equal(stack, monkeypatch):
    project = improving(stack, monkeypatch, pinned=False)
    runs = []
    for entry in (('improve',), ('skills', 'invoke')):
        _, found, _ = project.invoke(entry=entry)
        runs.append((found, Path(found['output'])))
    (cli, cli_run), (skill, skill_run) = runs
    assert cli['loop'] == skill['loop']
    assert entry_parity.document_differences(entry_parity.RECEIPT, cli, skill, cli_run, skill_run) == []
