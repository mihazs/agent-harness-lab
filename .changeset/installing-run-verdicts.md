---
"agent-harness-lab": patch
---

An installing `ahl improve` run whose final comparison was measured and not found worse ends with a verdict instead of `evaluation_failed`: `no_change` with `evaluation_no_improvement` (or `improvement_not_confirmed` when a fix showed but could not be confirmed), plus `baseline_at_least_as_good`, `candidate_lost` and `invalid_controls` where they apply.
