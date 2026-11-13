"""Per-root repeat consistency beside the fixed analysis; synthetic, descriptive, never a decision input."""
import contextlib
from dataclasses import replace
import io
import json

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import reliability
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from agent_harness_lab.evaluation.statistics import TrialRecord, analyze as fixed_analysis


def analyze(spec, rows):
    return fixed_analysis(spec, rows, include_reliability=True)


def plan(roots=40, repeats=3, families=('f',), min_roots=3):
    tasks = tuple((f't{f}{i}', f'r{f}{i}', f) for f in families for i in range(roots))
    return ExperimentSpec('synthetic-reliability', tasks, repeats, 13, 2 * len(tasks) * repeats, min_roots, .1, .05, .05,
                          tuple((f, 1 / len(families)) for f in families), 'b' * 64, 'c' * 64, 'g' * 64, 'i' * 64)


def records(spec, outcome, **changes):
    """`outcome(root, arm, repetition)` gives each scheduled trial's synthetic grade."""
    rows = []
    for trial in schedule(spec):
        values = dict(trial_id=trial['trial_id'], outcome=outcome(trial['root'], trial['version'], trial['repetition']),
                      status='graded', provenance='executable_grader', baseline_hash=spec.baseline_hash,
                      candidate_hash=spec.candidate_hash, grader_hash=spec.grader_hash, runtime_hash=spec.runtime_hash)
        rows.append(TrialRecord(**{**values, **changes}))
    return rows


def lift(root, arm, repetition):
    return arm == 'candidate' or root.endswith('0')


def test_same_mean_lift_with_different_repeat_consistency_is_distinguishable():
    spec = plan(40, 4)
    # Baseline always fails. Candidate A passes every repeat on half the roots; B passes two of four repeats on every root.
    steady = analyze(spec, records(spec, lambda r, arm, k: arm == 'candidate' and int(r[2:]) % 2 == 0))
    flaky = analyze(spec, records(spec, lambda r, arm, k: arm == 'candidate' and k % 2 == 0))
    assert steady['primary']['mean'] == flaky['primary']['mean'] == pytest.approx(.5)
    first, second = steady['repeat_reliability']['overall'], flaky['repeat_reliability']['overall']
    assert (first['candidate']['all_pass_roots'], first['candidate']['repeat_unstable_roots']) == (20, 0)
    assert (second['candidate']['all_pass_roots'], second['candidate']['repeat_unstable_roots']) == (0, 40)
    assert first['candidate']['lowest_root_success'] == 0 and second['candidate']['lowest_root_success'] == .5
    assert first['baseline'] == dict(all_pass_roots=0, all_fail_roots=40, lowest_root_success=0, repeat_unstable_roots=0)
    assert first['consistency'] == 'measured'


def test_roots_are_counted_not_repeats_and_single_repeat_consistency_is_not_measurable():
    spec = plan(10, 1)
    report = analyze(spec, records(spec, lambda r, arm, k: arm == 'candidate'))['repeat_reliability']
    assert (report['repetitions'], report['overall']['roots']) == (1, 10)
    assert report['overall']['consistency'] == 'not_measurable_single_repeat'
    assert report['overall']['candidate']['repeat_unstable_roots'] is None
    repeated = plan(10, 5)
    assert analyze(repeated, records(repeated, lambda r, arm, k: arm == 'candidate'))['repeat_reliability']['overall']['roots'] == 10


def test_worst_root_and_family_breakdown_expose_harm_hidden_by_the_mean():
    spec = plan(30, 3, families=('a', 'b'))
    # Family a gains on every root; one root of family b loses every repeat while the rest tie.
    def grade(root, arm, repeat):
        if root.startswith('ra'): return arm == 'candidate'
        return not (arm == 'candidate' and root == 'rb0')
    report = analyze(spec, records(spec, grade))['repeat_reliability']
    assert report['overall']['paired']['worst_root_difference'] == -1
    assert report['overall']['paired']['negative_difference_roots'] == 1
    assert report['families']['a']['paired'] == dict(worst_root_difference=1, best_root_difference=1,
                                                      negative_difference_roots=0, positive_difference_roots=30)
    assert report['families']['b']['paired']['worst_root_difference'] == -1
    assert report['families']['b']['candidate']['all_fail_roots'] == 1
    assert sum(item['roots'] for item in report['families'].values()) == report['overall']['roots']


def test_related_tasks_in_one_root_stay_one_root_and_task_repeats_are_checked_per_task():
    tasks = tuple((f't{i}{j}', f'r{i}', 'f') for i in range(6) for j in range(2))
    spec = ExperimentSpec('synthetic-reliability', tasks, 2, 13, 100, 3, .1, .05, .05, (('f', 1.0),),
                          'b' * 64, 'c' * 64, 'g' * 64, 'i' * 64)
    trials = {t['trial_id']: t for t in schedule(spec)}
    # Within each root the two tasks disagree, but each task is stable across its two repeats.
    rows = records(spec, lambda r, arm, k: None)
    rows = [replace(row, outcome=trials[row.trial_id]['task'].endswith('0')) for row in rows]
    overall = analyze(spec, rows)['repeat_reliability']['overall']
    assert overall['roots'] == 6
    assert overall['candidate']['repeat_unstable_roots'] == 0 and overall['candidate']['lowest_root_success'] == .5
    assert overall['candidate']['all_pass_roots'] == overall['candidate']['all_fail_roots'] == 0


def test_unknown_or_invalid_trials_get_no_profile_and_are_never_imputed():
    spec = plan(10, 3)
    complete = records(spec, lift)
    assert 'repeat_reliability' in analyze(spec, complete)
    ungraded = [replace(complete[0], status='error', outcome=None)] + complete[1:]
    assert 'repeat_reliability' not in analyze(spec, ungraded)
    assert analyze(spec, ungraded)['status'] == 'incomplete'
    assert 'repeat_reliability' not in analyze(spec, complete[1:])
    assert 'repeat_reliability' not in analyze(spec, records(spec, lift, provenance='model_judge'))
    critical = analyze(spec, [replace(complete[0], critical_regression=True)] + complete[1:])
    assert (critical['status'], 'repeat_reliability' in critical) == ('rejected', False)


def test_too_few_independent_roots_still_reports_descriptive_consistency_without_support():
    spec = plan(2, 3, min_roots=5)
    result = analyze(spec, records(spec, lift))
    assert (result['status'], result['reason']) == ('inconclusive', 'insufficient_independent_roots')
    assert result['repeat_reliability']['overall']['roots'] == 2


def test_profile_is_opt_in_and_does_not_change_the_decision_fields():
    spec = plan(100, 2)
    result = analyze(spec, records(spec, lift))
    assert 'repeat_reliability' not in fixed_analysis(spec, records(spec, lift))
    assert {k: v for k, v in result.items() if k != 'repeat_reliability'} == fixed_analysis(spec, records(spec, lift))
    stripped = {k: v for k, v in result.items() if k != 'repeat_reliability'}
    assert {'status', 'primary', 'families', 'reason', 'roots', 'repetitions', 'trials', 'multiplicity',
            'rare_harm_risk'} == set(stripped)
    assert result['repeat_reliability']['profile'] == reliability.PROFILE


def test_cli_recorded_analysis_reports_the_profile_and_stays_non_authoritative(tmp_path):
    spec = plan(40, 3)
    (tmp_path / 'plan.json').write_text(json.dumps(spec.to_dict()))
    rows = [dict(vars(row)) for row in records(spec, lift)]
    (tmp_path / 'records.json').write_text(json.dumps(rows))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(['experiment', '--spec', str(tmp_path / 'plan.json'), '--records', str(tmp_path / 'records.json')])
    value = json.loads(output.getvalue())
    assert code == 3
    assert value['repeat_reliability']['overall']['roots'] == 40
    assert value['promotion']['automatic'] is False and value['oracle_validity'] == 'unverified'
