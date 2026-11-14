# Optimization strategy registry

Reference for the Python API in `agent_harness_lab.optimization.strategies`. It
makes candidate-search strategies interchangeable under one authorization, budget
and selection contract. It is default-off and has no CLI surface.

## Registered strategies

| ID | Version | Slots (kind per proposal) | Notes |
|---|---|---|---|
| `no-op` | 1 | `no-op` | Unchanged comparator. The proposer is never called. |
| `single-revision` | 1 | `revise` | One localized revision, the documented refinement limit. |
| `reflective-gepa-inspired` | 1 | `add`, `add`, `simplify` | GEPA-inspired proposals plus a simplification slot. Not a reproduction of upstream GEPA. |

Registration is explicit and trusted: `register(StrategySpec(...))`. The registry
never imports modules, entry points, files or callbacks because they exist on a
path. Unknown IDs or versions raise `ValueError`. A third-party adapter needs its
own reviewed registration; none is included, and the core stays standard-library only.

## Running a strategy on the callback route

```python
run_strategy(strategy_id, version, path, baseline, proposer, evaluate, certify,
             budget, allowed_files, evidence, seed=0, variants=None)
```

- `evidence` must be a list of mappings that each carry `scope: "development"`.
  Anything else is refused before a state file exists.
- `proposer(request)` receives `index`, `kind`, a derived `seed`, the development
  evidence and the remaining non-reserved budget. It returns a `CandidateRecord` or
  `None`. A raised exception becomes a retained rejection.
- Proposals, evaluation, the untouchable final reserve, duplicate and mutation
  rejection, safety-first Pareto selection and the no-change tie rule are the
  existing `search` contract. The strategy cannot call certification itself.
- `seed` and `variants` are the only configuration keys. `variants` is at most the
  strategy's slot count. The state path gets a suffix derived from strategy ID,
  version and configuration, so a changed strategy or configuration is a new run and
  never reuses or resets earlier charges. Repeating a run resumes without
  re-executing charged work. The saved state does not record the budget it ran
  under, so a resumed run checks its saved charges against the budget you pass: it
  raises `ValueError` when they exceed the ceiling, or exceed the ceiling less the
  reserve before the final-reserve charge, or when the state records no charges.
- The result adds `strategy` (ID, version, configuration, identity, state path).
  Results from the callback route carry `evidence_source: "unverified_callback"`: the
  callback `safe` and metric values are not controller-verified evidence, and
  development points are selection heuristics, not proof of benefit.

## Running through the guarded controller route

```python
run_strategy(strategy_id, version, path, baseline, proposer, None, None, budget,
             allowed_files, evidence, controller=context, parent_deadline=None)
```

Pass a `HistorySearchContext` as `controller` to run the strategy through
[guarded development search](guarded-search.md) instead of the callback route. The
actual controller executes and independently verifies every candidate and the fresh
development confirmation, so `evaluate` and `certify` must be `None`: a raw callback
result cannot rank or select there, and passing one raises `ValueError`. The callback
route in turn needs both callbacks, and `parent_deadline` is valid only with a
controller. Everything above still applies (development-only evidence, the slot
kinds, derived seeds, per-configuration state paths, the no-op that never calls the
proposer). The guarded contracts also apply: baseline must be the unchanged
authorized context, `variants` is at most 3, the state directory must be owner-only
and a repeat must pass exactly the same ceiling and reserve or it raises
`ValueError`. The result carries `evidence_source: "controller_verified_development_only"`
with automatic promotion, publication and adoption authority all `False`. A favorable
status is `development_recommended_for_review`: development evidence after a fresh
confirmation, not untouched certification.

## Comparing strategies under one budget

```python
compare_strategies(directory, baseline, proposer, evaluate, ceiling, reserve,
                   allowed_files, evidence, strategies=DEFAULT_COMPARISON, seed=0)
```

Runs each listed `(strategy_id, version)` through `run_strategy` with the same
baseline, proposer, evaluator, development evidence, authorized files and seed, and
a fresh `Budget(ceiling, reserve)` per strategy. The default is the three built-ins.
Bad strategy pairs, versions, configuration, budgets or non-development evidence are
refused before any state file exists. Each strategy keeps its own state file in
`directory`, so repeating the call resumes without re-executing charged work.
Repeat it with the same `ceiling` and `reserve`: a repeat whose budget the saved
charges exceed raises `ValueError` instead of reporting that budget as matched, and
nothing further runs. A larger budget resumes the saved result without re-running
it, so the report shows the larger budget over charges made under the smaller one.
To compare under a different budget, use a new `directory`.

`compare_strategies(..., controller=context, parent_deadline=None)` runs every listed
strategy through the guarded controller route with `evaluate` as `None`. Each row is
built from the guarded state: `charges` come from its durable stage charges
(including `development_confirmation`), `rejections` list the invalid and duplicate
attempts with their reasons, and `disposition` is the guarded status
(`development_recommended_for_review`, `no_change`, `rejected_or_inconclusive`,
`interrupted`, `budget_exhausted` or `blocked`). `selected` and `finalists` hold only
candidates recommended for review after a fresh confirmation; a candidate whose
confirmation fails, or whose run was interrupted, is not a finalist. The report's
`evidence_source` is `controller_verified_development_only`. On this route a repeat
with a different ceiling or reserve is refused by the guarded state, and an interrupted
strategy stays `interrupted` without redispatching or refunding its charges.

The report has one row per strategy and never only the winner:

- `charges`: the shared `ceiling` and `reserve`, actual `spent` and `by_kind`. A
  strategy with fewer slots may spend less; a smaller ceiling stops larger pools with
  `budget_exhausted`. Charges are search stage units. `measured_usage` is `None`
  because tokens and currency are not measured on this route; unknown stays unknown.
- `attempts`: every evaluated candidate, baseline included, with digest, `safe` and
  metrics. Candidates with missing metrics are listed and cannot be selected.
- `rejections`: every retained invalid, duplicate or mutated proposal.
- `disposition`: the search status (`no_change` on ties, `budget_exhausted`,
  `blocked`) or `finalist_deferred_to_protected_evaluation`, with `selected`.

No comparison calls certification: the finalist stub only records the choice, and the
final-reserve charge marks that slot. `finalists` lists the deferred choices across
strategies. Several finalists need predeclared evaluator access and multiplicity
handling before any protected evaluation, so do not reuse one untouched set for each.
`advantage_claim` is always `False`: development points are selection heuristics. A
claim beyond extra inference needs a separately specified equal-budget retry
comparator, which this report does not provide.

## Not covered yet

A supported model-backed reflective proposer and measured token or cost accounting
remain separate work: the controller route charges stage units, not tokens or currency,
and `measured_usage` stays `None`. The equal-budget retry comparator, successive halving,
recombination and optional DSPy adapters remain opt-in experiments.
