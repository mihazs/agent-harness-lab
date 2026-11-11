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

## Not covered yet

This slice provides the registry and fake-strategy integration only. A supported
model-backed reflective proposer, measured token or cost accounting, the guarded
controller route and the matched-budget strategy comparison are separate work.
Successive halving and recombination remain opt-in experiments.
