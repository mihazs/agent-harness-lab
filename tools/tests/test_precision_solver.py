"""Inverse frozen-plan precision on public synthetic plans; descriptive, never authority."""
import contextlib
from dataclasses import replace
import io
import itertools
import json
from pathlib import Path
import tempfile
import unittest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import benchmark
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.precision import preflight
if __package__:
    from . import test_preservation_analysis as preservation
else:
    import test_preservation_analysis as preservation


def spec(sizes, weights=None, *, target=None, alpha=.05, effect=.1, family_margin=.05, min_roots=2,
         repeats=1, max_trials=None, extra_tasks=()):
    weights = weights or {family: 1 / len(sizes) for family in sizes}
    tasks = tuple((f'{f}-t{i}', f'{f}-r{i}', f) for f in sorted(sizes) for i in range(sizes[f])) + tuple(extra_tasks)
    return ExperimentSpec('synthetic-precision-solver', tasks, repeats, 13, max_trials or 4000 * repeats,
        min_roots, effect, family_margin, alpha, tuple(weights.items()), 'b' * 64, 'c' * 64, 'g' * 64, 'r' * 64,
        precision_half_width=target)


def solved(value, **options):
    return preflight(value, **options)['sample_size_plan']


def required(value, **options):
    return {row['family']: row['required_roots'] for row in solved(value, **options)['families']}


def accepted(sizes, weights, **options):
    # Resize with an ample new trial ceiling; feasibility is judged by unmodified preflight.
    report = preflight(spec(sizes, weights, **options))
    return report['status'] == 'feasible' and report['precision_target_met'] is not False


class PrecisionSolverTests(unittest.TestCase):
    def test_single_family_solution_meets_target_and_one_fewer_root_does_not(self):
        for target in (.1, .15, .2, .3, .5, .9, 1):
            with self.subTest(target=target):
                n = required(spec({'a': 2}, target=target))['a']
                self.assertTrue(accepted({'a': n}, None, target=target))
                fewer = preflight(spec({'a': n - 1}, target=target))
                self.assertEqual(fewer['status'], 'infeasible')
                if target < .9:  # above this the strict primary margin, not the target, binds
                    self.assertGreater(fewer['endpoints']['family:a']['half_width'], target)
        self.assertEqual(required(spec({'a': 100}, target=.1))['a'], 877)

    def test_weighted_primary_binding_is_minimum_total_by_exhaustive_preflight(self):
        weights = {'a': .8, 'b': .2}
        for target in (None, .9):
            with self.subTest(target=target):
                plan = solved(spec({'a': 3, 'b': 3}, weights, effect=.5, target=target))
                sizes = {row['family']: row['required_roots'] for row in plan['families']}
                self.assertEqual(plan['status'], 'feasible')
                self.assertIn('primary_endpoint', {row['binding_constraint'] for row in plan['families']})
                self.assertTrue(accepted(sizes, weights, effect=.5, target=target))
                for family in sizes:
                    self.assertFalse(accepted({**sizes, family: sizes[family] - 1}, weights, effect=.5, target=target))
                total = sum(sizes.values())
                for a in range(1, total - 1):
                    self.assertFalse(accepted({'a': a, 'b': total - 1 - a}, weights, effect=.5, target=target))
        plan = solved(spec({'a': 3, 'b': 3, 'c': 3}, min_roots=40))
        self.assertEqual(plan['required_roots'], 40)
        sizes = {row['family']: row['required_roots'] for row in plan['families']}
        self.assertTrue(accepted(sizes, None, min_roots=40))
        for a, b in itertools.product(range(1, 39), repeat=2):
            if 39 - a - b > 0: self.assertFalse(accepted({'a': a, 'b': b, 'c': 39 - a - b}, None, min_roots=40))

    def test_repeats_and_related_tasks_never_become_independent_roots(self):
        one, twenty = solved(spec({'a': 50}, target=.2)), solved(spec({'a': 50}, target=.2, repeats=20))
        self.assertEqual(one['families'], twenty['families'])
        self.assertEqual((one['repeats']['minimum_for_target'], twenty['repeats']['minimum_for_target']), (1, 1))
        self.assertEqual(twenty['minimum_planned_trials'], 20 * one['minimum_planned_trials'])
        related = spec({'a': 50}, target=.2, extra_tasks=[(f'related-{i}', 'a-r0', 'a') for i in range(30)])
        plan = solved(related)
        self.assertEqual((plan['declared_roots'], plan['families']), (50, one['families']))
        self.assertEqual(plan['independent_unit'], 'declared_root')

    def test_solution_is_monotone_deterministic_and_bounded(self):
        previous = 0
        for step in range(45):
            n = required(spec({'a': 2}, target=1 - step * .02))['a']
            self.assertGreaterEqual(n, previous); previous = n
        previous = 0
        for alpha in (.5, .2, .1, .05, .01, .001):
            n = sum(required(spec({'a': 2, 'b': 2}, {'a': .7, 'b': .3}, alpha=alpha, target=.4)).values())
            self.assertGreaterEqual(n, previous); previous = n
        previous = 0
        for effect in (0, .2, .4, .6, .8):
            n = solved(spec({'a': 2, 'b': 2}, {'a': .9, 'b': .1}, effect=effect))['required_roots']
            self.assertGreaterEqual(n, previous); previous = n
        value = spec({'a': 10, 'b': 5}, {'a': .6, 'b': .4}, target=.3)
        self.assertEqual(solved(value), solved(value))

    def test_numerically_extreme_valid_plans_stay_infeasible_not_errors(self):
        for options in (dict(target=1e-160), dict(target=1e-200), dict(target=.1, alpha=5e-324)):
            with self.subTest(**options):
                report = preflight(spec({'a': 4, 'b': 4}, **options))
                self.assertEqual(report['status'], 'infeasible')
                plan = report['sample_size_plan']
                self.assertEqual((plan['status'], plan['required_roots']), ('infeasible', None))
                self.assertFalse(plan['publication_allowed']);self.assertEqual(plan['authority'], 'descriptive_only')

    def test_caps_and_budgets_report_explicit_infeasibility(self):
        plan = solved(spec({'a': 10}, target=.05))
        self.assertEqual((plan['status'], plan['reasons'], plan['required_roots']),
            ('infeasible', ['family:a:exceeds_1000_task_cap'], None))
        plan = solved(spec({'a': 10, 'b': 10, 'c': 10}, target=.12))
        self.assertEqual(plan['reasons'], ['primary:exceeds_1000_task_cap'])
        plan = solved(spec({'a': 877}, target=.1), trial_limit=1753)
        self.assertEqual((plan['status'], plan['reasons'], plan['required_roots']),
            ('infeasible', ['trial_ceiling_below_minimum_trials'], 877))
        self.assertEqual(solved(spec({'a': 877}, target=.1), trial_limit=1754)['status'], 'feasible')

    def test_frozen_cohort_members_are_kept_and_small_cohorts_are_refused(self):
        base = replace(preservation.plan(), precision_half_width=.3)
        value, frozen = preservation.freeze(base, members=[t[0] for t in base.tasks[:200]])
        plan = solved(value, cohort_manifest_json=frozen['manifest_json'])
        self.assertEqual(plan['status'], 'feasible')
        self.assertEqual(plan['families'][0]['required_roots'], 200)
        self.assertEqual(plan['families'][0]['binding_constraint'], 'frozen_cohort_members')
        self.assertEqual(plan['cohorts'][0]['declared_roots'], 200)
        self.assertLessEqual(plan['cohorts'][0]['required_roots'], 200)
        value, frozen = preservation.freeze(base, members=[t[0] for t in base.tasks[:10]])
        plan = solved(value, cohort_manifest_json=frozen['manifest_json'])
        self.assertEqual(plan['reasons'], ['cohort:protected:frozen_membership_below_required_roots'])
        self.assertFalse(plan['cohorts'][0]['resizable_by_solver'])

    def test_report_is_descriptive_private_and_leaves_plan_unchanged(self):
        value = spec({'a': 20, 'b': 20}, {'a': .5, 'b': .5}, target=.3, max_trials=80)
        digest = value.digest
        report = preflight(value)
        plan = report['sample_size_plan']
        self.assertEqual(value.digest, digest); self.assertEqual(plan['plan_sha256'], digest)
        self.assertEqual(report['status'], 'infeasible'); self.assertFalse(report['execution_allowed'])
        self.assertEqual(plan['status'], 'infeasible')  # frozen 80-trial ceiling cannot afford the solution
        self.assertEqual((plan['profile'], plan['authority'], plan['oracle_authority']),
            ('precision-sample-size-v1', 'descriptive_only', 'not_established'))
        for key in ('automatic_budget_change', 'bank_mutation', 'plan_mutation', 'automatic_adoption', 'publication_allowed'):
            self.assertIs(plan[key], False)
        public = dict(publication_allowed=True, provenance=dict(source_kind='synthetic', approved_publication=True,
            source_ref='synthetic:precision-solver', derived_from_private=False))
        self.assertFalse(benchmark.public_export_allowed([{**plan, **public}]))
        self.assertFalse(benchmark.public_export_allowed([{**report, **public}]))

    def test_cli_preflight_reports_versioned_plan(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'plan.json'
            path.write_text(json.dumps(spec({'a': 100}, target=.1).to_dict()))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(['experiment', '--spec', str(path), '--preflight']), 3)
            plan = json.loads(output.getvalue())['sample_size_plan']
            self.assertEqual((plan['profile'], plan['required_roots'], plan['families'][0]['additional_roots']),
                ('precision-sample-size-v1', 877, 777))


if __name__ == '__main__': unittest.main()
