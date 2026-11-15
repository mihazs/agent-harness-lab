"""Seeded operating characteristics of the sequential profile beside the fixed one; synthetic only.

Acceptance criteria are predeclared here, before the simulations were run, with the same
Wilson 95% bounds the fixed power tests use: a null model keeps the upper bound of the
false-support rate at or below .05, a large benefit keeps the lower bound of its support rate at
or above .9, and a large harm keeps the lower bound of its rejection rate at or above .9.
"""
from dataclasses import replace
import json
from unittest import mock

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import benchmark, sequential as core, sequential_power as power
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.power import MODEL_SCHEMA
from agent_harness_lab.evaluation.sequential_power import simulate


def plan(roots=16, *, max_roots=None, repeats=1, families=None, **kwargs):
    """A sequential plan; ``families`` maps a family to its root count and defaults to one family."""
    counts = families or {'family': roots}
    tasks = tuple((f'{f}-t{i}', f'{f}-r{i}', f) for f, n in counts.items() for i in range(n))
    return ExperimentSpec('synthetic-sequential-power', tasks, repeats, 13, 2 * len(tasks) * repeats,
        min_roots=2, effect_margin=.05, family_margin=.1, alpha=.05,
        family_weights=tuple((f, 1 / len(counts)) for f in counts),
        baseline_hash='b' * 64, candidate_hash='c' * 64, grader_hash='g' * 64, runtime_hash='i' * 64,
        sequential=core.config(max_roots or len(tasks), 3, .25), **kwargs)


def outcome(baseline, effect, **kwargs):
    return dict(schema=MODEL_SCHEMA, baseline_success=baseline, candidate_effect=effect, **kwargs)


def count(report, status, *, fixed=False):
    return (report['fixed_comparator'] if fixed else report['sequential'])['dispositions'][status]['count']


def test_replay_is_deterministic_and_reports_roots_apart_from_repeats():
    spec, declared = plan(12, repeats=2), outcome(.4, .3, root_spread=.2, repeat_correlation=.5)
    first = simulate(spec, declared, simulations=20, seed=5)
    assert first == simulate(spec, declared, simulations=20, seed=5)
    assert first != simulate(spec, declared, simulations=20, seed=6)
    assert first['plan_sha256'] == spec.digest and first['profile'] == power.PROFILE
    assert (first['declared_roots'], first['max_roots'], first['repetitions']) == (12, 12, 2)
    assert first['fixed_comparator']['planned_trials'] == 2 * 12 * 2
    assert first['fixed_comparator']['planned_roots'] == 12
    for side in (first['sequential'], first['fixed_comparator']):
        assert sum(d['count'] for d in side['dispositions'].values()) == 20
        assert sum(side['reasons'].values()) == 20
    assert first['sequential']['observed_roots']['count'] == 20
    assert first['sequential']['observed_roots']['max'] <= 12
    assert first['sequential']['observed_trials']['max'] <= 2 * 12 * 2
    assert (first['executions'], first['automatic_promotion'], first['publication_allowed']) == (0, False, False)
    assert (first['automatic_budget_change'], first['root_independence']) == (False, 'assumed_by_model')
    assert first['analysis_mode'] == 'sequential_anytime_finite_bank'
    assert first['fixed_comparator']['analysis_mode'] == 'fixed_final'
    assert first['model_sha256'] == simulate(spec, declared, simulations=1, seed=0)['model_sha256']
    json.dumps(first, allow_nan=False)


def test_null_model_meets_predeclared_false_support_criterion_under_repeated_inspection():
    # Predeclared: 300 null simulations, seed 29, heterogeneous roots and correlated repeats. The
    # controller inspects after every root block; a supported stop must still be a true statement
    # about the finite bank's realized mean, with the Wilson upper bound of false support <= .05.
    report = simulate(plan(16), outcome(.5, 0, root_spread=.3, repeat_correlation=.5), simulations=300, seed=29)
    claims = report['finite_bank_claims']
    assert claims['false_support']['wilson_95'][1] <= .05
    assert claims['false_harm_call']['wilson_95'][1] <= .05
    assert report['declared_effect'] == 'null'
    assert report['supported_rate']['interpretation'] == 'support_rate_under_declared_model'
    # Every supported stop was backed by realized benefit, so supports track a real finite-bank mean.
    assert count(report, 'supported') <= claims['realized_benefit_criteria_met']['count']
    assert count(report, 'rejected') <= claims['realized_family_or_primary_harm']['count']


def test_partial_inspection_of_a_null_bank_never_supports_and_reports_the_saved_trials():
    report = simulate(plan(24, max_roots=12, repeats=2), outcome(.5, 0, root_spread=.3, repeat_correlation=.5),
                      simulations=300, seed=29)
    assert report['finite_bank_claims']['false_support']['wilson_95'][1] <= .05
    assert report['sequential']['reasons'] == {'maximum_roots': 300}
    assert count(report, 'inconclusive') == 300
    assert report['comparison']['mean_trial_fraction_of_fixed'] == pytest.approx(.5)
    assert report['comparison']['mean_trials_saved'] == pytest.approx(48)


def test_large_benefit_has_predeclared_power_and_stops_before_the_fixed_comparator_finishes():
    report = simulate(plan(16), outcome(.1, .8), simulations=100, seed=3)
    assert report['supported_rate']['wilson_95'][0] >= .9
    assert report['supported_rate']['interpretation'] == 'power'
    assert report['finite_bank_claims']['false_support']['count'] == 0
    assert report['comparison']['mean_trial_fraction_of_fixed'] < .75
    assert report['comparison']['mean_trials_saved'] > 0
    assert report['sequential']['by_disposition']['supported']['observed_roots']['max'] < 16
    assert report['sequential']['reasons'] == {'sequential_benefit': 100}


def test_large_harm_is_rejected_with_predeclared_rate_and_no_false_harm_call():
    report = simulate(plan(16), outcome(.9, -.8), simulations=100, seed=3)
    assert report['sequential']['dispositions']['rejected']['wilson_95'][0] >= .9
    assert report['finite_bank_claims']['false_harm_call']['count'] == 0
    assert count(report, 'supported') == 0
    assert report['supported_rate']['interpretation'] == 'support_rate_under_declared_model'


def test_one_family_harm_hidden_by_a_large_primary_gain_is_not_supported():
    spec = plan(families={'a': 10, 'b': 10})
    declared = outcome(.3, .6, family_effects={'b': -.6})
    report = simulate(spec, declared, simulations=100, seed=3)
    assert report['declared_effect'] == 'mixed'
    assert report['supported_rate']['interpretation'] == 'support_rate_under_declared_model'
    assert report['finite_bank_claims']['false_support']['count'] == 0
    assert count(report, 'rejected') >= 65
    assert count(report, 'supported') <= report['finite_bank_claims']['realized_benefit_criteria_met']['count']


def test_a_sparse_family_below_the_root_minimum_never_supports_even_a_large_benefit():
    report = simulate(plan(families={'common': 8, 'rare': 1}), outcome(.1, .8), simulations=60, seed=3)
    assert count(report, 'supported') == 0 and count(report, 'supported', fixed=True) == 0
    assert report['sequential']['reasons'] == {'maximum_roots': 60}


def test_any_critical_regression_rejects_at_the_first_critical_trial():
    report = simulate(plan(16), outcome(.5, .5, critical_regression_probability=1), simulations=40, seed=3)
    assert count(report, 'rejected') == 40 and report['sequential']['reasons'] == {'critical_regression': 40}
    assert report['sequential']['observed_roots']['max'] == 1
    assert report['sequential']['observed_trials']['max'] <= 2
    assert count(report, 'rejected', fixed=True) == 40


def test_lost_grades_end_an_inspection_and_never_create_support():
    # Predeclared invariants: a root with an unknown grade ends the controller with `incomplete`;
    # the fixed comparator loses the same grades; and neither profile supports a null on the strength of them.
    spec = plan(16)
    total = simulate(spec, outcome(.1, .8, candidate_ungraded_probability=1), simulations=20, seed=3)
    assert total['sequential']['reasons'] == {'unknown_root_outcome': 20}
    assert total['fixed_comparator']['reasons'] == {'unknown_or_invalid_grade': 20}
    assert total['sequential']['observed_roots']['max'] == 1 and total['completion']['fully_graded']['count'] == 0
    assert count(total, 'incomplete') == count(total, 'incomplete', fixed=True) == 20
    null = simulate(spec, outcome(.5, 0, root_spread=.3, repeat_correlation=.5, baseline_ungraded_probability=.05,
                                  candidate_ungraded_probability=.05), simulations=300, seed=29)
    assert null['finite_bank_claims']['false_support']['wilson_95'][1] <= .05
    assert null['completion']['declared'] == dict(baseline_ungraded_probability=.05, candidate_ungraded_probability=.05)


def test_early_stopping_keeps_support_that_a_fixed_final_analysis_loses_to_lost_grades():
    # Predeclared: 100 simulations, seed 3, 40 roots (enough for the fixed analysis to support a large
    # benefit whenever every grade is present), a large benefit and a 2% candidate grade loss.
    report = simulate(plan(40), outcome(.1, .8, candidate_ungraded_probability=.02), simulations=100, seed=3)
    whole = report['completion']['fully_graded']['count']
    assert count(report, 'supported', fixed=True) == whole  # every fully graded fixed run is supported
    assert count(report, 'supported') > count(report, 'supported', fixed=True)
    assert count(report, 'supported') + count(report, 'incomplete') == 100
    assert report['finite_bank_claims']['false_support']['count'] == 0


def test_models_without_declared_grade_loss_keep_their_sequential_reports():
    report = simulate(plan(8), outcome(.5, .3), simulations=5, seed=1)
    assert 'completion' not in report
    zero = simulate(plan(8), outcome(.5, .3, baseline_ungraded_probability=0), simulations=5, seed=1)
    assert 'completion' not in zero and zero['sequential'] == report['sequential']
    assert zero['fixed_comparator'] == report['fixed_comparator']


def test_sequential_report_is_private_and_never_public_exportable():
    report = simulate(plan(8), outcome(.5, .3), simulations=5, seed=1)
    public = dict(publication_allowed=True, provenance=dict(source_kind='synthetic', approved_publication=True,
                  source_ref='synthetic:sequential-power', derived_from_private=False))
    assert not benchmark.public_export_allowed([report])
    assert not benchmark.public_export_allowed([{**report, **public}])
    assert any('unknown outcomes' in line for line in report['limitations'])
    assert any('different targets' in line for line in report['limitations'])


def test_invalid_inputs_and_unsupported_plans_fail_closed():
    spec = plan(8)
    with pytest.raises(ValueError, match='sequential plan'):
        power.simulate(replace(spec, sequential=None), outcome(.5, 0))
    with pytest.raises(ValueError, match='cohort'):
        simulate(replace(spec, cohort_manifest_sha256='a' * 64), outcome(.5, 0))
    with pytest.raises(ValueError, match='frozen experiment spec'):
        simulate(spec.to_dict(), outcome(.5, 0))
    for bad in ({}, dict(outcome(.5, 0), schema='other'), dict(outcome(.5, 0), extra=1), outcome(True, 0),
                outcome(.5, float('nan')), outcome(.5, 0, family_effects=dict(unknown=.1))):
        with pytest.raises(ValueError):
            simulate(spec, bad, simulations=1)
    for simulations in (0, 10001, True, 1.0):
        with pytest.raises(ValueError, match='simulations'):
            simulate(spec, outcome(.5, 0), simulations=simulations)
    with pytest.raises(ValueError, match='seed'):
        simulate(spec, outcome(.5, 0), seed='1')


def test_oversized_simulations_are_refused_before_any_draw_or_schedule():
    spec = plan(40)
    with mock.patch.object(power.core, 'schedule', side_effect=AssertionError('schedule was built')), \
            mock.patch.object(power, '_draw', side_effect=AssertionError('outcomes were drawn')):
        with pytest.raises(ValueError, match='analyzed schedule rows'):
            simulate(spec, outcome(.5, 0), simulations=power.MAX_ANALYZED_ROWS // (40 * 80) + 1)
        with pytest.raises(ValueError, match='simulated trials'):
            simulate(plan(1000, repeats=1), outcome(.5, 0), simulations=1001)


def test_cli_routes_a_sequential_plan_to_the_sequential_simulator_and_keeps_fixed_plans(tmp_path, capsys):
    spec, declared = plan(10), outcome(.2, .6, repeat_correlation=.5)
    (tmp_path / 'plan.json').write_text(json.dumps(spec.to_dict()))
    (tmp_path / 'model.json').write_text(json.dumps(declared))
    argv = ['experiment', '--spec', str(tmp_path / 'plan.json'), '--power-model', str(tmp_path / 'model.json'),
            '--simulations', '12', '--simulation-seed', '4']
    assert main(argv) == 0
    value = json.loads(capsys.readouterr().out)
    assert value == json.loads(json.dumps(simulate(spec, declared, simulations=12, seed=4)))
    assert value['profile'] == 'sequential-root-power-simulation-v1'
    fixed_spec = replace(spec, sequential=None)
    (tmp_path / 'fixed.json').write_text(json.dumps(fixed_spec.to_dict()))
    assert main([*argv[:2], str(tmp_path / 'fixed.json'), *argv[3:]]) == 0
    assert json.loads(capsys.readouterr().out)['profile'] == 'fixed-root-power-simulation-v1'
