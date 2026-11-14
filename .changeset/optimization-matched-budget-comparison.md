---
"agent-harness-lab": minor
---

Add `compare_strategies` to the optimization strategy registry (part of #12): it runs the no-op, single-revision and reflective GEPA-inspired built-ins under one matched stage ceiling and reserve, with the same baseline, evaluator, development evidence and seed, and reports every evaluated candidate, retained rejection and actual stage charge per strategy instead of only the winner. Selected finalists are deferred to a protected evaluation, so a comparison never certifies or reuses an untouched set; a repeat that resumes saved charges the requested ceiling and reserve do not cover is refused instead of reported as matched; tokens and currency stay unmeasured (`None`) and no advantage is claimed. See `docs/optimization-strategies.md`.
