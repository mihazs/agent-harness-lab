"""Seeded operating characteristics of the conditional sequential profile beside the fixed profile.

Each simulation draws one set of synthetic potential outcomes and judges it twice with the
unchanged decision engines: ``sequential.analyze`` after every observed root block under the
controller's own stopping rule, and ``statistics.analyze`` over every declared task. Estimates
hold only under the caller-declared synthetic outcome model; they are not observed improvement,
verified root independence or adoption authority.
"""
from collections import Counter, defaultdict
from dataclasses import replace
from fractions import Fraction
import hashlib
import json
import math
import random
import statistics
from . import sequential as core
from .plan import ExperimentSpec, schedule
from .power import DISPOSITIONS, MAX_PLANNED_TRIALS, MAX_SIMULATED_TRIALS, Z95, _declare, _draw, _effect, wilson
from .statistics import analyze as fixed_analyze

PROFILE = 'sequential-root-power-simulation-v1'
# Each inspected root rebuilds the sequential schedule, so one call is bounded by schedule rows built.
MAX_ANALYZED_ROWS = 4_000_000


def _summary(values):
    """Mean with a normal-approximation 95% Monte Carlo interval and the observed spread."""
    n, mean = len(values), sum(values) / len(values)
    error = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1) / n) if n > 1 else None
    return dict(count=n, mean=mean, normal_95=None if error is None else [mean - Z95 * error, mean + Z95 * error],
                min=min(values), median=statistics.median(values), max=max(values))


def _rates(counts, total):
    return {status: dict(count=n, rate=n / total, wilson_95=wilson(n, total)) for status, n in counts.items()}


def _realized(spec, potential, weights):
    """Exact finite-bank family means and weighted primary of root-block paired differences."""
    blocks = defaultdict(list)
    for task, root, family in spec.tasks:
        for repetition in range(spec.repeats):
            blocks[family, root].append(int(potential[task, repetition, 'candidate'].outcome)
                                        - int(potential[task, repetition, 'baseline'].outcome))
    means = defaultdict(list)
    for (family, _), differences in blocks.items(): means[family].append(Fraction(sum(differences), len(differences)))
    families = {family: sum(values) / len(values) for family, values in means.items()}
    return families, sum(weights[family] * value for family, value in families.items())


def _sequential(spec, binding, potential, rows_by_root):
    """Run the controller's stopping rule over a drawn root order; return (analysis, roots, trials)."""
    seen, analysis, observed = [], None, 0
    for root in binding['root_order'][:spec.sequential['max_roots']]:
        for row in rows_by_root[root]:
            seen.append(replace(potential[row['task'], row['repetition'], row['version']], trial_id=row['trial_id']))
            if seen[-1].critical_regression: break  # the controller stops at the first critical trial
        observed += 1
        analysis = core.analyze(spec, seen, binding)
        if core.terminal(analysis): return analysis, observed, len(seen)
    return dict(analysis, status='inconclusive', reason='maximum_roots'), observed, len(seen)


def simulate(spec, outcome_model, *, simulations=200, seed=0):
    """Estimate sequential and fixed disposition rates, stopping roots and trials under a declared model."""
    if not isinstance(spec, ExperimentSpec): raise ValueError('a frozen experiment spec is required')
    spec.validate()
    if spec.sequential is None: raise ValueError('sequential power simulation requires a sequential plan')
    if spec.cohort_manifest_sha256 is not None:
        raise ValueError('cohort-bound preservation analysis is not simulated')
    families, declared = _declare(spec, outcome_model, simulations, seed)
    sequential_trials, fixed_trials = core.planned_trials(spec), 2 * len(spec.tasks) * spec.repeats
    if fixed_trials > MAX_PLANNED_TRIALS:
        raise ValueError(f'sequential power simulation exceeds {MAX_PLANNED_TRIALS} planned fixed-comparator trials per simulation')
    if simulations * fixed_trials > MAX_SIMULATED_TRIALS:
        raise ValueError(f'sequential power simulation exceeds {MAX_SIMULATED_TRIALS} simulated trials')
    if simulations * spec.sequential['max_roots'] * sequential_trials > MAX_ANALYZED_ROWS:
        raise ValueError(f'sequential power simulation exceeds {MAX_ANALYZED_ROWS} analyzed schedule rows')
    # The fixed comparator runs every declared task; same potential outcomes, unchanged fixed engine.
    fixed = replace(spec, sequential=None, max_trials=fixed_trials)
    fixed_plan = schedule(fixed)
    template, roots = core.create_binding(spec, []), sorted({root for _, root, _ in spec.tasks})
    weights = {family: core.rational(weight) for family, weight in spec.family_weights}
    weights = {family: weight / sum(weights.values()) for family, weight in weights.items()}
    effect_margin, family_margin = core.rational(spec.effect_margin), core.rational(spec.family_margin)
    rng, sequential_counts, fixed_counts = random.Random(seed), dict.fromkeys(DISPOSITIONS, 0), dict.fromkeys(DISPOSITIONS, 0)
    reasons, fixed_reasons, agreement = Counter(), Counter(), Counter()
    stop_roots, stop_trials, by_status = [], [], defaultdict(lambda: ([], []))
    realized_primary, truth = [], Counter()
    for _ in range(simulations):
        drawn = _draw(fixed, fixed_plan, declared, rng)
        potential = {(t['task'], t['repetition'], t['version']): record for t, record in zip(fixed_plan, drawn)}
        # A seeded uniform permutation stands in for the controller's system-random draw.
        order = roots[:]; rng.shuffle(order)
        binding = dict(template, root_order=order)
        rows_by_root = defaultdict(list)
        for row in core.schedule(spec, binding): rows_by_root[row['root']].append(row)
        result, observed, trials = _sequential(spec, binding, potential, rows_by_root)
        comparator = fixed_analyze(fixed, drawn)
        # The sequential claim concerns this bank's realized root-block means, not the model's expectation.
        families_truth, primary_truth = _realized(spec, potential, weights)
        benefit = primary_truth > effect_margin and all(v >= -family_margin for v in families_truth.values())
        harm = primary_truth < -family_margin or any(v < -family_margin for v in families_truth.values())
        realized_primary.append(float(primary_truth))
        truth['benefit_true'] += benefit; truth['harm_true'] += harm
        truth['false_support'] += result['status'] == 'supported' and not benefit
        truth['false_harm_call'] += result['reason'] == 'preservation_or_family_harm' and not harm
        sequential_counts[result['status']] += 1; fixed_counts[comparator['status']] += 1
        reasons[result['reason']] += 1; fixed_reasons[comparator['reason']] += 1
        agreement[f"{result['status']}/{comparator['status']}"] += 1
        stop_roots.append(observed); stop_trials.append(trials)
        by_status[result['status']][0].append(observed); by_status[result['status']][1].append(trials)
    supported, mean_trials = _rates(sequential_counts, simulations)['supported'], sum(stop_trials) / simulations
    direction = _effect(declared, families)
    claim = lambda name: dict(count=truth[name], rate=truth[name] / simulations, wilson_95=wilson(truth[name], simulations))
    return dict(schema_version=1, profile=PROFILE, plan_sha256=spec.digest,
        model=declared, model_sha256=hashlib.sha256(json.dumps(declared, sort_keys=True).encode()).hexdigest(),
        simulation_seed=seed, simulations=simulations, declared_effect=direction,
        analysis_method=core.METHOD, analysis_mode='sequential_anytime_finite_bank',
        decision_engine='agent_harness_lab.evaluation.sequential.analyze',
        declared_roots=len(roots), max_roots=spec.sequential['max_roots'], repetitions=spec.repeats,
        sequential=dict(dispositions=_rates(sequential_counts, simulations), reasons=dict(sorted(reasons.items())),
            planned_trials_at_max_roots=sequential_trials, observed_roots=_summary(stop_roots),
            observed_trials=_summary(stop_trials),
            by_disposition={status: dict(observed_roots=_summary(values[0]), observed_trials=_summary(values[1]))
                            for status, values in sorted(by_status.items())}),
        fixed_comparator=dict(analysis_method='weighted_root_Hoeffding', analysis_mode='fixed_final',
            decision_engine='agent_harness_lab.evaluation.statistics.analyze',
            dispositions=_rates(fixed_counts, simulations), reasons=dict(sorted(fixed_reasons.items())),
            planned_trials=fixed_trials, planned_roots=len(roots)),
        finite_bank_claims=dict(estimand=core.ESTIMAND, realized_primary_difference=_summary(realized_primary),
            realized_benefit_criteria_met=claim('benefit_true'), realized_family_or_primary_harm=claim('harm_true'),
            false_support=claim('false_support'), false_harm_call=claim('false_harm_call')),
        comparison=dict(mean_trial_fraction_of_fixed=mean_trials / fixed_trials, mean_trials_saved=fixed_trials - mean_trials,
            sequential_over_fixed_disposition_counts=dict(sorted(agreement.items()))),
        supported_rate=dict(estimate=supported['rate'], wilson_95=supported['wilson_95'],
            interpretation='power' if direction == 'beneficial' else 'support_rate_under_declared_model'),
        monte_carlo_interval=dict(rates='Wilson_score_95', means='normal_approximation_95'),
        power='simulated_under_declared_model', root_independence='assumed_by_model',
        model_authority='caller_declared_synthetic', executions=0, automatic_budget_change=False,
        automatic_promotion=False, publication_allowed=False,
        limitations=['Rates hold only under the declared synthetic outcome model; real task dependence may differ.',
            'Simulated roots are independent by construction and drawn without regard to the sampling order.',
            'The sequential target is this bank\'s realized root-block mean, which a random null draw can place above the margin; '
            'only false_support measures a wrong sequential claim, and fixed-comparator rates carry the fixed-profile interpretation.',
            'The two profiles estimate different targets under different assumptions; matching dispositions are not equivalent claims.',
            'Every simulated trial is graded: unknown outcomes, partial roots and time-budget exhaustion are not simulated.',
            'No worker, model or grader is run; nothing is observed, certified or adopted.',
            'A low supported rate is not evidence of noninferiority; rates and means carry Monte Carlo uncertainty.',
            'The plan, margins, method and budgets are unchanged; no trials are added.'])
