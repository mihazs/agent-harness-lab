"""Versioned, explicitly registered optimization strategies behind the shared search contracts."""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from agent_harness_lab.monitoring.state import atomic_json
from .budget import Budget
from .search import CandidateRecord, search

CONFIG_KEYS = {'seed', 'variants'}


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    version: int
    edit_surfaces: tuple
    kinds: tuple  # One proposal kind per search slot; the slot count is the proposal bound.
    description: str
    evidence_schema: str = 'development-evidence-v1'
    checkpoint_schema: str = 'search-state-v1'
    uses_model: bool = False

    @property
    def max_proposals(self):
        return len(self.kinds)

    def config(self, **options):
        """Canonical deterministic configuration; unknown or malformed values are rejected."""
        if set(options) - CONFIG_KEYS:
            raise ValueError('unknown strategy configuration key')
        seed, variants = options.get('seed', 0), options.get('variants', self.max_proposals)
        if type(seed) is not int or type(variants) is not int or not 1 <= variants <= self.max_proposals:
            raise ValueError('invalid strategy configuration')
        return dict(seed=seed, variants=variants)

    def identity(self, config):
        body = json.dumps(dict(id=self.strategy_id, version=self.version, config=config), sort_keys=True)
        return hashlib.sha256(body.encode()).hexdigest()


# Explicit trusted registration only: nothing here imports modules, entry points or paths.
_REGISTRY = {}


def register(spec):
    if type(spec) is not StrategySpec or not spec.strategy_id or type(spec.version) is not int or not spec.kinds:
        raise ValueError('invalid strategy spec')
    key = (spec.strategy_id, spec.version)
    if key in _REGISTRY:
        raise ValueError('strategy already registered')
    _REGISTRY[key] = spec
    return spec


def get(strategy_id, version):
    try:
        return _REGISTRY[(strategy_id, version)]
    except (KeyError, TypeError):
        raise ValueError('unsupported strategy or version') from None


def registered():
    return sorted(_REGISTRY)


NO_OP = register(StrategySpec('no-op', 1, (), ('no-op',), 'Unchanged comparator; keeps the baseline.'))
SINGLE_REVISION = register(StrategySpec('single-revision', 1, ('instruction', 'skill'), ('revise',),
    'One localized revision, the documented refinement limit.', uses_model=True))
REFLECTIVE = register(StrategySpec('reflective-gepa-inspired', 1, ('instruction', 'skill'),
    ('add', 'add', 'simplify'),
    'GEPA-inspired reflective proposals plus a simplification slot; not a reproduction of upstream GEPA.',
    uses_model=True))


def _development_only(evidence):
    rows = evidence if isinstance(evidence, (list, tuple)) else None
    if rows is None or any(type(row) is not dict or row.get('scope') != 'development' for row in rows):
        raise ValueError('strategies receive development evidence only')
    return list(rows)


def run_strategy(strategy_id, version, path, baseline, proposer, evaluate, certify, budget, allowed_files, evidence,
                 *, controller=None, parent_deadline=None, **options):
    """Run one strategy through the shared guarded budget/selection contract.

    `proposer(request)` returns a CandidateRecord or None. The request carries only development evidence,
    the slot kind, a derived seed and the remaining non-reserved budget; it cannot reach certification.
    A changed strategy, version or configuration writes a new state file, so charges are never reset or reused.
    With a `HistorySearchContext` as `controller`, every candidate runs and is verified by the actual controller
    (`evaluate` and `certify` must be None: a raw callback result cannot rank or select there) and the result is
    controller-verified development evidence; `parent_deadline` bounds that route only.
    """
    spec = get(strategy_id, version)
    config = spec.config(**options)
    if controller is None and parent_deadline is not None:
        raise ValueError('a parent deadline bounds the controller route only')
    if controller is not None and (evaluate is not None or certify is not None):
        raise ValueError('the controller route verifies candidates itself; raw callbacks are refused')
    if controller is None and not (callable(evaluate) and callable(certify)):
        raise ValueError('the callback route needs evaluate and certify')
    evidence = _development_only(evidence)
    identity = spec.identity(config)
    path = Path(path)
    path = path.with_name(f'{path.stem}-{identity[:16]}{path.suffix}')

    def propose(index):
        request = dict(index=index, kind=spec.kinds[index - 1],
            seed=int(hashlib.sha256(f'{config["seed"]}:{index}'.encode()).hexdigest()[:8], 16),
            evidence=evidence, remaining=budget.ceiling - budget.reserve - budget.spent)
        if spec.strategy_id == 'no-op':
            return CandidateRecord(f'{baseline.candidate_id}-noop', (baseline.candidate_id,), baseline.mechanism,
                dict(baseline.changes), baseline.evidence_refs, baseline.hypothesis, baseline.falsifier)
        try:
            return proposer(request)
        except Exception:  # A failed proposal is retained as a rejection by the search, never a crash.
            return None

    result = search(path, baseline, propose, evaluate, certify, budget, allowed_files, enabled=True,
                    variants=config['variants'], controller=controller, parent_deadline=parent_deadline)
    # A saved callback state ignores the new budget, so check its spend against it; guarded search compares exactly.
    if controller is None and result.get('resumed_without_execution'):
        events = result.get('budget_events')
        if not isinstance(events, list):
            raise ValueError('saved search state has no recorded budget')
        spent = sum(event['amount'] for event in events)
        non_final = sum(event['amount'] for event in events if not event['final'])
        if spent > budget.ceiling or non_final > budget.ceiling - budget.reserve:
            raise ValueError(f'saved search state spent {spent} ({non_final} before the final reserve), '
                             f'more than the requested ceiling {budget.ceiling} and reserve {budget.reserve} allow')
    return dict(result, strategy=dict(id=spec.strategy_id, version=version, config=config, identity=identity,
                                      state_path=str(path)))


DEFAULT_COMPARISON = (('no-op', 1), ('single-revision', 1), ('reflective-gepa-inspired', 1))
# A comparison never reaches final evaluation: several finalists must not reuse one untouched set.
_DEFERRED = dict(status='deferred_to_protected_evaluation')


_REJECTIONS = dict(invalid='invalid_or_unauthorized_proposal', duplicate='duplicate_patch')


def _events(result):
    return result['budget']['events'] if 'budget' in result else result.get('budget_events', [])


def _rejections(result):
    if 'attempts' not in result:  # The callback route keeps its rejections; guarded attempts hold them as stages.
        return result.get('rejections', [])
    return [dict(index=a['index'], **(dict(candidate_id=a['record']['candidate_id']) if a['record'] else {}),
                 reason=_REJECTIONS[a['stage']]) for a in result['attempts'] if a['stage'] in _REJECTIONS]


RETRY_ESTIMAND = ('selected development candidate versus the unchanged baseline retried unconditionally, a count fixed '
                  'before any retry runs; both sides are selection-inflated development observations, not a treatment effect')


def _retry_baseline(path, baseline, evaluate, retries, ceiling, reserve):
    """Evaluate the unchanged baseline `retries` times under its own budget, charging each retry before dispatch."""
    path = Path(path)
    if path.exists():  # A charged retry is never silently redispatched: an unfinished run stays interrupted.
        state = json.loads(path.read_text())
        if state.get('schema_version') != 1:
            raise ValueError('unsupported retry state')
        return dict(state, status='interrupted' if state['status'] == 'retrying' else state['status'],
                    resumed_without_execution=True)
    budget = Budget(ceiling, reserve)
    run_id = hashlib.sha256(str(path.absolute()).encode()).hexdigest()[:16]
    state = dict(schema_version=1, status='retrying', retries=retries, attempts=[], budget_events=budget.events)

    def save(status=None):
        state['status'] = status or state['status']
        atomic_json(path, state)
        return dict(state, resumed_without_execution=False)
    for index in range(1, retries + 1):
        if not budget.consume('retry_trial', 1, f'{run_id}:retry:{index}'):
            return save('budget_exhausted')
        save()
        frozen = baseline.digest
        result = evaluate(baseline)
        if baseline.digest != frozen:
            return save('blocked')
        state['attempts'].append(dict(candidate_id=f'{baseline.candidate_id}-retry-{index}', digest=frozen,
                                      safe=result.get('safe') is True, metrics=result.get('metrics', {})))
        save()
    return save('complete')


def _exceeds_best_retry(selected, retry):
    """True or False only when every retry is comparable; a missing or mismatched metric leaves it unknown (None)."""
    keys, rows = set(selected['metrics']), retry['attempts']
    numeric = lambda metrics: set(metrics) == keys and all(type(v) in (int, float) and math.isfinite(v) for v in metrics.values())
    feasible = [row['metrics'] for row in rows if row['safe']]
    if retry['status'] != 'complete' or not keys or not feasible or not all(numeric(row['metrics']) for row in [selected, *rows]):
        return None
    mine = selected['metrics']
    return all(all(mine[k] >= other[k] for k in keys) and any(mine[k] > other[k] for k in keys) for other in feasible)


def compare_strategies(directory, baseline, proposer, evaluate, ceiling, reserve, allowed_files, evidence,
                       strategies=DEFAULT_COMPARISON, seed=0, *, controller=None, parent_deadline=None,
                       retry_comparator=False):
    """Run strategies under one matched stage ceiling and report every executed attempt, not only winners.

    Each strategy gets the same baseline, proposer, evaluator, development evidence, authorized files and a
    fresh `Budget(ceiling, reserve)`. The report lists actual stage charges, every evaluated candidate, every
    retained rejection and the finalists left for a protected evaluation whose access and multiplicity
    handling are declared elsewhere. Charges are stage units on the callback route: tokens and currency are
    unmeasured, so `measured_usage` stays None. Development points are heuristics, never an advantage claim.
    With a `HistorySearchContext` as `controller` (and `evaluate` None) each strategy runs through the guarded
    controller route; a favorable strategy is then `development_recommended_for_review` after a fresh development
    confirmation, still deferred to a protected evaluation and never certified here.
    With `retry_comparator=True` (callback route only) each strategy that selected a finalist also gets the unchanged
    baseline re-evaluated once per non-final stage unit it charged, under its own `Budget(ceiling, reserve)`; the row's
    `exceeds_best_retry` is True only when the finalist dominates every feasible retry, and None when anything is unknown.
    """
    if type(retry_comparator) is not bool:
        raise ValueError('retry_comparator must be True or False')
    if retry_comparator and controller is not None:
        raise ValueError('the equal-budget retry comparator is callback route only')
    strategies = list(strategies)
    if not strategies or len(set(strategies)) != len(strategies) or any(
            type(key) is not tuple or len(key) != 2 for key in strategies):
        raise ValueError('comparison needs distinct (strategy, version) pairs')
    specs = [get(*key) for key in strategies]
    for spec in specs:
        spec.config(seed=seed)
    evidence = _development_only(evidence)
    budgets = [Budget(ceiling, reserve) for _ in specs]  # Validated together before any state exists.
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    rows, finalists = [], []
    for spec, budget in zip(specs, budgets):
        result = run_strategy(spec.strategy_id, spec.version, directory/f'{spec.strategy_id}.json', baseline, proposer,
                              evaluate, None if controller is not None else lambda candidate: _DEFERRED, budget, allowed_files,
                              evidence, controller=controller, parent_deadline=parent_deadline, seed=seed)
        by_kind = {}
        for event in _events(result):
            by_kind[event['kind']] = by_kind.get(event['kind'], 0) + event['amount']
        deferred = result.get('certification') == _DEFERRED
        finalist = deferred or result['status'] == 'development_recommended_for_review'
        if finalist:
            finalists.append(dict(strategy=spec.strategy_id, candidate_id=result['finalist']))
        row = dict(strategy=result['strategy'], disposition='finalist_deferred_to_protected_evaluation' if deferred else result['status'],
            selected=result['finalist'] if finalist else None,
            charges=dict(ceiling=ceiling, reserve=reserve, spent=sum(by_kind.values()), by_kind=by_kind),
            measured_usage=None, resumed_without_execution=result.get('resumed_without_execution', False),
            attempts=[dict(candidate_id=c['candidate_id'], digest=c['digest'], safe=c['safe'], metrics=c['metrics'])
                      for c in result.get('candidates', [])],
            rejections=_rejections(result))
        if retry_comparator:
            row['retry_comparator'] = None
            if finalist:
                units = sum(e['amount'] for e in _events(result) if not e['final'])
                state = Path(result['strategy']['state_path'])
                retry = _retry_baseline(state.with_name(f'retry-{state.stem}-k{units}{state.suffix}'), baseline, evaluate,
                                        units, ceiling, reserve)
                selected = next(c for c in result['candidates'] if c['candidate_id'] == result['finalist'])
                spent = sum(e['amount'] for e in retry['budget_events'])
                row['retry_comparator'] = dict(estimand=RETRY_ESTIMAND, matched_stage_units=units, status=retry['status'],
                    charges=dict(ceiling=ceiling, reserve=reserve, spent=spent, by_kind=dict(retry_trial=spent)),
                    attempts=retry['attempts'], exceeds_best_retry=_exceeds_best_retry(selected, retry),
                    resumed_without_execution=retry['resumed_without_execution'])
        rows.append(row)
    return dict(matched=dict(ceiling=ceiling, reserve=reserve, seed=seed), strategies=rows, finalists=finalists,
                advantage_claim=False,
                evidence_source='controller_verified_development_only' if controller is not None else 'unverified_callback')
