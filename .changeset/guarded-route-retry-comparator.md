---
"agent-harness-lab": minor
---

Add the equal-budget retry comparator to the guarded controller route of strategy comparisons. `compare_strategies(..., controller=context, retry_comparator=True)` now re-runs the unchanged baseline against itself through the actual controller, once per non-final controller run a strategy made, under a new `baseline_retry` stage that accepts only the empty patch. Each retry reports its observed primary effect (an effect that is missing or not finite stays unqualified, never zero), is re-verified from its controller receipt on the run and on every repeat, is charged before dispatch under its own budget, and is never redispatched after a failure or interruption. `exceeds_best_retry` is true only when the selected candidate beats every qualified retry and stays unknown otherwise; no advantage claim is made. The route was refused before this release.
