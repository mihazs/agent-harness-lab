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

## Running a strategy

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
  re-executing charged work.
- The result adds `strategy` (ID, version, configuration, identity, state path).
  Results from this route carry `evidence_source: "unverified_callback"`: the
  callback `safe` and metric values are not controller-verified evidence, and
  development points are selection heuristics, not proof of benefit.

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

A supported model-backed reflective proposer, measured token or cost accounting and
the guarded controller route for strategies remain separate work. Successive halving,
recombination and optional DSPy adapters remain opt-in experiments.
