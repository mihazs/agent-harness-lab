"""Seeded Monte Carlo operating characteristics of the frozen fixed-final analysis.

Every simulated record set is judged by the unchanged ``statistics.analyze`` decision
engine. Estimates hold only under the caller-declared synthetic outcome model: they are
not observed improvement, verified root independence or adoption authority.
"""
import hashlib
import json
import math
import random
from .plan import ExperimentSpec, schedule
from .statistics import TrialRecord, analyze

PROFILE = 'fixed-root-power-simulation-v1'
MODEL_SCHEMA = 'synthetic-paired-outcome-model-v1'
MAX_TASKS = 1000
MAX_SIMULATIONS = 10000
MAX_SIMULATED_TRIALS = 2_000_000
# analyze rebuilds the schedule for every simulation, so one plan is bounded separately.
MAX_PLANNED_TRIALS = 100_000
DISPOSITIONS = ('supported', 'rejected', 'inconclusive', 'incomplete', 'blocked')
Z95 = 1.959963984540054
_RANGES = dict(baseline_success=(0, 1), candidate_effect=(-1, 1), root_spread=(0, 1),
               repeat_correlation=(0, 1), critical_regression_probability=(0, 1))
_DEFAULTS = dict(family_effects={}, root_spread=0, repeat_correlation=0, critical_regression_probability=0)


def _number(name, value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'outcome model {name} must be a finite number in [{low}, {high}]')
    return float(value)


def model(value, families):
    """Validate one declared outcome model against the plan's frozen families."""
    if not isinstance(value, dict) or value.get('schema') != MODEL_SCHEMA:
        raise ValueError(f'outcome model requires schema {MODEL_SCHEMA}')
    unknown = set(value) - {'schema', *_RANGES, 'family_effects'}
    if unknown: raise ValueError('unknown outcome model fields: ' + ', '.join(sorted(unknown)))
    if not {'baseline_success', 'candidate_effect'} <= set(value):
        raise ValueError('outcome model requires baseline_success and candidate_effect')
    merged = {**_DEFAULTS, **value}
    normalized = dict(schema=MODEL_SCHEMA, **{k: _number(k, merged[k], *r) for k, r in _RANGES.items()})
    effects = merged['family_effects']
    if not isinstance(effects, dict) or set(effects) - set(families):
        raise ValueError('family_effects may only name frozen plan families')
    normalized['family_effects'] = {f: _number('family effect ' + f, effects[f], -1, 1) for f in sorted(effects)}
    return normalized


def _effect(declared, families):
    effects = [declared['family_effects'].get(f, declared['candidate_effect']) for f in families]
    if all(e == 0 for e in effects): return 'null'
    if all(e >= 0 for e in effects): return 'beneficial'
    if all(e <= 0 for e in effects): return 'harmful'
    return 'mixed'


def wilson(count, total):
    p, z2 = count / total, Z95 * Z95
    centre, spread = p + z2 / (2 * total), Z95 * math.sqrt(p * (1 - p) / total + z2 / (4 * total * total))
    low, high = (centre - spread) / (1 + z2 / total), (centre + spread) / (1 + z2 / total)
    # The exact score interval reaches the boundary at zero or total counts.
    return [0.0 if count == 0 else max(0.0, low), 1.0 if count == total else min(1.0, high)]


def _draw(spec, plan, declared, rng):
    roots = sorted({(family, root) for _, root, family in spec.tasks})
    probability = {}
    for family, root in roots:
        spread = declared['root_spread']
        base = min(1.0, max(0.0, declared['baseline_success'] + (rng.uniform(-spread, spread) if spread else 0)))
        effect = declared['family_effects'].get(family, declared['candidate_effect'])
        probability[root, 'baseline'] = base
        probability[root, 'candidate'] = min(1.0, max(0.0, base + effect))
    # One shared draw per root and arm models outcomes correlated across repeats and related tasks.
    shared = {key: rng.random() < p for key, p in sorted(probability.items())}
    records = []
    for trial in plan:
        key = (trial['root'], trial['version'])
        outcome = shared[key] if rng.random() < declared['repeat_correlation'] else rng.random() < probability[key]
        critical = trial['version'] == 'candidate' and rng.random() < declared['critical_regression_probability']
        records.append(TrialRecord(trial['trial_id'], outcome, 'graded', 'executable_grader', spec.baseline_hash,
                                   spec.candidate_hash, spec.grader_hash, spec.runtime_hash, critical))
    return records


def simulate(spec, outcome_model, *, simulations=200, seed=0):
    """Estimate disposition rates of the frozen fixed analysis under a declared outcome model."""
    if not isinstance(spec, ExperimentSpec): raise ValueError('a frozen experiment spec is required')
    spec.validate()
    if spec.sequential is not None: raise ValueError('power simulation covers only the fixed-final analysis')
    if spec.root_similarity_sha256 is not None: raise ValueError('power simulation does not support root similarity evidence')
    if spec.cohort_manifest_sha256 is not None:
        raise ValueError('cohort-bound preservation analysis is not simulated')
    if len(spec.tasks) > MAX_TASKS: raise ValueError('power simulation exceeds the 1000-task bank cap')
    if type(simulations) is not int or not 1 <= simulations <= MAX_SIMULATIONS:
        raise ValueError(f'simulations must be an integer in [1, {MAX_SIMULATIONS}]')
    if type(seed) is not int: raise ValueError('simulation seed must be an integer')
    families = sorted({family for _, _, family in spec.tasks})
    declared = model(outcome_model, families)
    planned = 2 * len(spec.tasks) * spec.repeats
    if planned > MAX_PLANNED_TRIALS:
        raise ValueError(f'power simulation exceeds {MAX_PLANNED_TRIALS} planned trials per simulation')
    if simulations * planned > MAX_SIMULATED_TRIALS:
        raise ValueError(f'power simulation exceeds {MAX_SIMULATED_TRIALS} simulated trials')
    plan = schedule(spec)
    rng, counts, reasons = random.Random(seed), dict.fromkeys(DISPOSITIONS, 0), {}
    for _ in range(simulations):
        result = analyze(spec, _draw(spec, plan, declared, rng))
        counts[result['status']] += 1
        reasons[result['reason']] = reasons.get(result['reason'], 0) + 1
    rates = {status: dict(count=n, rate=n / simulations, wilson_95=wilson(n, simulations)) for status, n in counts.items()}
    direction = _effect(declared, families)
    supported = rates['supported']
    return dict(schema_version=1, profile=PROFILE, plan_sha256=spec.digest, model=declared,
        model_sha256=hashlib.sha256(json.dumps(declared, sort_keys=True).encode()).hexdigest(),
        simulation_seed=seed, simulations=simulations, declared_effect=direction,
        analysis_method='weighted_root_Hoeffding', analysis_mode='fixed_final',
        decision_engine='agent_harness_lab.evaluation.statistics.analyze',
        declared_roots=len({root for _, root, _ in spec.tasks}), repetitions=spec.repeats,
        planned_trials_per_simulation=len(plan), simulated_trials=simulations * len(plan),
        dispositions=rates, reasons=dict(sorted(reasons.items())),
        supported_rate=dict(estimate=supported['rate'], wilson_95=supported['wilson_95'],
            interpretation='power' if direction == 'beneficial' else 'false_recommendation_rate'
            if direction in ('null', 'harmful') else 'mixed_family_effects'),
        monte_carlo_interval='Wilson_score_95', power='simulated_under_declared_model',
        root_independence='assumed_by_model', model_authority='caller_declared_synthetic',
        executions=0, automatic_budget_change=False, automatic_promotion=False, publication_allowed=False,
        limitations=['Rates hold only under the declared synthetic outcome model; real task dependence may differ.',
            'Simulated roots are independent by construction; repeats share one root-level draw per arm.',
            'No worker, model or grader is run; nothing is observed, certified or adopted.',
            'A low supported rate is not evidence of noninferiority; rates carry Monte Carlo uncertainty.',
            'The plan, margins, method and budgets are unchanged; no trials are added.'])
