"""Seeded power and false-recommendation simulation of the fixed analysis; synthetic only."""
from collections import defaultdict
from dataclasses import replace
import contextlib
import io
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest import mock

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import benchmark, power, sequential
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from agent_harness_lab.evaluation.power import MODEL_SCHEMA, simulate, wilson
from agent_harness_lab.evaluation.statistics import bound


def plan(n=100, repeats=1, families=('family',)):
    tasks = tuple((f't{f}{i}', f'r{f}{i}', f) for f in families for i in range(n))
    return ExperimentSpec('synthetic-power', tasks, repeats, 13, 2 * len(tasks) * repeats, 3, .1, .05, .05,
                          tuple((f, 1 / len(families)) for f in families), 'b' * 64, 'c' * 64, 'g' * 64, 'i' * 64)


def outcome(baseline, effect, **kwargs):
    return dict(schema=MODEL_SCHEMA, baseline_success=baseline, candidate_effect=effect, **kwargs)


class PowerSimulationTests(unittest.TestCase):
    def test_fixed_interval_coverage_requires_independent_roots(self):
        # Predeclared synthetic check: 1,000 draws, 100 roots, seed 394, alpha .05
        # split across primary and family endpoints; true paired mean is zero.
        rng = random.Random(394)
        covered = [0, 0]
        for _ in range(1000):
            independent = [1 if rng.getrandbits(1) else -1 for _ in range(100)]
            shared_family = [1 if rng.getrandbits(1) else -1] * 100
            for index, values in enumerate((independent, shared_family)):
                interval = bound(values, [.01] * 100, .025)
                covered[index] += interval['low'] <= 0 <= interval['high']
        self.assertGreaterEqual(covered[0], 950)
        self.assertEqual(covered[1], 0)

    def test_replay_is_deterministic_and_separates_roots_from_repeats(self):
        spec, declared = plan(20, 3), outcome(.4, .2, root_spread=.2, repeat_correlation=.5)
        first = simulate(spec, declared, simulations=30, seed=5)
        self.assertEqual(first, simulate(spec, declared, simulations=30, seed=5))
        self.assertEqual(first['plan_sha256'], spec.digest)
        self.assertEqual((first['declared_roots'], first['repetitions']), (20, 3))
        self.assertEqual((first['planned_trials_per_simulation'], first['simulated_trials']), (120, 3600))
        self.assertEqual(sum(d['count'] for d in first['dispositions'].values()), 30)
        self.assertEqual(sum(first['reasons'].values()), 30)
        other = simulate(spec, declared, simulations=30, seed=6)
        self.assertEqual((other['model_sha256'], other['plan_sha256']), (first['model_sha256'], first['plan_sha256']))
        self.assertEqual((first['executions'], first['automatic_promotion'], first['publication_allowed']), (0, False, False))

    def test_null_with_correlated_repeats_meets_predeclared_false_recommendation_criterion(self):
        # Predeclared before running: 300 null simulations, seed 29, correlated repeats
        # within heterogeneous roots; the Wilson 95% upper bound of the supported
        # (false recommendation) rate must be <= .05. Repeats never add roots.
        for repeats, correlation in ((1, 0), (5, 1)):
            with self.subTest(repeats=repeats, correlation=correlation):
                report = simulate(plan(40, repeats), outcome(.5, 0, root_spread=.3, repeat_correlation=correlation),
                                  simulations=300, seed=29)
                self.assertEqual(report['declared_effect'], 'null')
                self.assertEqual(report['supported_rate']['interpretation'], 'false_recommendation_rate')
                self.assertLessEqual(report['supported_rate']['wilson_95'][1], .05)
                self.assertEqual(report['declared_roots'], 40)

    def test_large_benefit_is_detected_with_stated_monte_carlo_uncertainty(self):
        # Predeclared: 100 simulations, seed 31; Wilson 95% lower bound of power >= .9.
        report = simulate(plan(100), outcome(.1, .8), simulations=100, seed=31)
        self.assertEqual(report['supported_rate']['interpretation'], 'power')
        self.assertGreaterEqual(report['supported_rate']['wilson_95'][0], .9)
        self.assertEqual(report['power'], 'simulated_under_declared_model')

    def test_underpowered_effect_stays_inconclusive_with_explicit_uncertainty(self):
        report = simulate(plan(100), outcome(.5, .1), simulations=60, seed=37)
        self.assertEqual(report['dispositions']['inconclusive']['count'], 60)
        self.assertEqual(report['supported_rate']['estimate'], 0)
        self.assertGreater(report['supported_rate']['wilson_95'][1], 0)
        self.assertEqual(report['reasons'], {'uncertainty_or_margin_not_met': 60})

    def test_known_harm_and_family_harm_trigger_rejection(self):
        harm = simulate(plan(100), outcome(.9, -.8), simulations=60, seed=41)
        self.assertEqual(harm['dispositions']['supported']['count'], 0)
        self.assertGreaterEqual(harm['dispositions']['rejected']['wilson_95'][0], .9)
        # A primary average near zero cannot hide one harmed family.
        mixed = simulate(plan(100, families=('a', 'b')), outcome(.5, 0, family_effects=dict(a=.5, b=-.5)),
                         simulations=40, seed=43)
        self.assertEqual(mixed['declared_effect'], 'mixed')
        self.assertEqual(mixed['supported_rate']['interpretation'], 'mixed_family_effects')
        self.assertEqual(mixed['dispositions']['supported']['count'], 0)
        self.assertEqual(mixed['dispositions']['rejected']['count'], 40)

    def test_critical_regressions_block_despite_large_benefit(self):
        report = simulate(plan(100), outcome(.1, .8, critical_regression_probability=.2), simulations=40, seed=47)
        self.assertEqual(report['dispositions']['rejected']['count'], 40)
        self.assertEqual(report['reasons'], {'critical_regression': 40})

    def test_lost_grades_withhold_the_decision_and_are_reported_not_imputed(self):
        # Predeclared invariants, not tuned rates: with no critical regression, a simulation that
        # lost any grade is exactly an incomplete one, and the rest keep their full-grade decision.
        spec = plan(100)
        report = simulate(spec, outcome(.1, .8, candidate_ungraded_probability=.005), simulations=100, seed=7)
        completion, dispositions = report['completion'], report['dispositions']
        whole = completion['fully_graded']['count']
        self.assertEqual(dispositions['incomplete']['count'], 100 - whole)
        self.assertEqual(report['reasons'].get('unknown_or_invalid_grade', 0), 100 - whole)
        self.assertEqual(dispositions['supported']['count'], whole)  # a large benefit is supported whenever fully graded
        self.assertEqual(completion['declared'], dict(candidate_ungraded_probability=.005))
        self.assertEqual((completion['planned_trials'], completion['loss_model']), (200, 'independent_per_trial_by_arm'))
        self.assertTrue(0 < whole < 100 and 0 < completion['mean_ungraded_trials'] < 3)
        self.assertEqual(report, simulate(spec, outcome(.1, .8, candidate_ungraded_probability=.005), simulations=100, seed=7))

    def test_total_or_one_sided_grade_loss_never_supports_a_candidate(self):
        spec = plan(20)
        everything = simulate(spec, outcome(.1, .8, baseline_ungraded_probability=1), simulations=20, seed=7)
        self.assertEqual(everything['reasons'], {'unknown_or_invalid_grade': 20})
        self.assertEqual(everything['completion']['mean_ungraded_trials'], 20)
        self.assertEqual(everything['completion']['fully_graded']['count'], 0)
        # Candidate trials that vanish while a harmful candidate runs cannot hide the harm as support.
        hidden = simulate(spec, outcome(.9, -.8, candidate_ungraded_probability=.3), simulations=60, seed=9)
        self.assertEqual(hidden['dispositions']['supported']['count'], 0)
        # An unknown trial carries no grader verdict, so a critical flag on it is not observed.
        critical = simulate(spec, outcome(.1, .8, critical_regression_probability=1, candidate_ungraded_probability=1),
                            simulations=10, seed=9)
        self.assertEqual(critical['reasons'], {'unknown_or_invalid_grade': 10})

    def test_models_without_declared_grade_loss_keep_their_reports(self):
        spec, plain = plan(10), outcome(.4, .3, repeat_correlation=.5)
        legacy = simulate(spec, plain, simulations=20, seed=3)
        self.assertNotIn('completion', legacy)
        zero = simulate(spec, dict(plain, baseline_ungraded_probability=0), simulations=20, seed=3)
        self.assertNotIn('completion', zero)
        self.assertEqual(zero['dispositions'], legacy['dispositions'])
        self.assertEqual(power.model(plain, ['family']).keys() & set(power.UNGRADED), set())
        for bad in (dict(plain, baseline_ungraded_probability=-.1), dict(plain, candidate_ungraded_probability=1.5),
                    dict(plain, candidate_ungraded_probability=True), dict(plain, candidate_ungraded_probability=float('nan'))):
            with self.subTest(model=bad), self.assertRaises(ValueError):
                simulate(spec, bad, simulations=1)

    def test_invalid_models_specs_and_budgets_fail_closed(self):
        spec = plan(10)
        for bad in ({}, dict(outcome(.5, 0), schema='other'), dict(outcome(.5, 0), extra=1), {'schema': MODEL_SCHEMA},
                    outcome(True, 0), outcome(.5, float('nan')), outcome(1.5, 0), outcome(.5, 0, root_spread=-.1),
                    outcome(.5, 0, family_effects=dict(unknown=.1)), outcome(.5, 0, family_effects=[]),
                    outcome(.5, 0, repeat_correlation=2)):
            with self.subTest(model=bad), self.assertRaises(ValueError):
                simulate(spec, bad, simulations=1)
        for count in (0, 10001, True, 1.0):
            with self.subTest(simulations=count), self.assertRaises(ValueError):
                simulate(spec, outcome(.5, 0), simulations=count)
        with self.assertRaises(ValueError): simulate(spec, outcome(.5, 0), seed='1')
        with self.assertRaisesRegex(ValueError, 'simulated trials'): simulate(plan(1000), outcome(.5, 0), simulations=1001)
        with self.assertRaisesRegex(ValueError, 'cohort'):
            simulate(replace(spec, cohort_manifest_sha256='a' * 64), outcome(.5, 0))
        sequential_spec = replace(spec, max_trials=20, sequential=sequential.config(10, 3, .25))
        with self.assertRaisesRegex(ValueError, 'fixed-final'): simulate(sequential_spec, outcome(.5, 0))
        with self.assertRaises(ValueError): simulate(spec.to_dict(), outcome(.5, 0))

    def test_oversized_plans_are_refused_before_any_schedule_is_built(self):
        unscheduled = mock.patch.object(power, 'schedule', side_effect=AssertionError('schedule was built'))
        for spec, simulations in ((plan(1000, 10 ** 7), 1), (plan(2, 50001), 1), (plan(1000), 1001)):
            with self.subTest(repeats=spec.repeats, simulations=simulations), unscheduled, \
                    self.assertRaisesRegex(ValueError, 'trials'):
                simulate(spec, outcome(.5, 0), simulations=simulations)

    def test_repeat_correlation_and_root_spread_shape_drawn_outcomes(self):
        spec = plan(20, 200)
        trials = schedule(spec)

        def outcomes(**kwargs):
            declared = power.model(outcome(.5, 0, **kwargs), ['family'])
            drawn = power._draw(spec, trials, declared, random.Random(3))
            grouped = defaultdict(list)
            for trial, record in zip(trials, drawn):
                grouped[trial['root'], trial['version']].append(record.outcome)
            return grouped

        self.assertTrue(all(len(set(v)) == 1 for v in outcomes(repeat_correlation=1).values()))
        self.assertTrue(any(len(set(v)) > 1 for v in outcomes(repeat_correlation=0).values()))
        def baseline_rates(spread):
            return [sum(v) / len(v) for (_, arm), v in outcomes(root_spread=spread).items() if arm == 'baseline']
        # 200 independent repeats estimate each root's base probability to within about .035.
        self.assertTrue(all(abs(rate - .5) < .15 for rate in baseline_rates(0)))
        spread = baseline_rates(1)
        self.assertLess(min(spread), .2)
        self.assertGreater(max(spread), .8)

    def test_wilson_reference_values(self):
        self.assertEqual(wilson(0, 100)[0], 0)
        self.assertEqual(wilson(100, 100)[1], 1)
        self.assertAlmostEqual(wilson(0, 100)[1], .0369935, places=6)
        low, high = wilson(50, 100)
        self.assertAlmostEqual(low, .4038315, places=6)
        self.assertAlmostEqual(high, .5961685, places=6)

    def test_cli_simulation_options_and_private_export_guard(self):
        spec, declared = plan(10, 2), outcome(.3, .4, repeat_correlation=.5)
        with tempfile.TemporaryDirectory() as folder:
            spec_path, model_path = Path(folder) / 'plan.json', Path(folder) / 'model.json'
            spec_path.write_text(json.dumps(spec.to_dict())); model_path.write_text(json.dumps(declared))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(['experiment', '--spec', str(spec_path), '--power-model', str(model_path),
                                       '--simulations', '25', '--simulation-seed', '4']), 0)
            value = json.loads(output.getvalue())
            self.assertEqual(value, json.loads(json.dumps(simulate(spec, declared, simulations=25, seed=4))))
            for argv in (['--preflight', '--simulations', '5'], ['--preflight', '--simulation-seed', '1'],
                         ['--preflight', '--power-model', str(model_path)],
                         ['--power-model', str(model_path), '--cohorts', str(model_path)]):
                with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), \
                        contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stopped:
                    main(['experiment', '--spec', str(spec_path), *argv])
                self.assertEqual(stopped.exception.code, 2)
        public = dict(publication_allowed=True, provenance=dict(source_kind='synthetic', approved_publication=True,
                      source_ref='synthetic:power', derived_from_private=False))
        self.assertFalse(benchmark.public_export_allowed([{**value, **public}]))
        stripped = {k: v for k, v in value.items() if k != 'plan_sha256'}
        self.assertFalse(benchmark.public_export_allowed([{**stripped, **public}]))


if __name__ == '__main__':
    unittest.main()
