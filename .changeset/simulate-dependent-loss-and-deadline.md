---
"agent-harness-lab": minor
---

The power-simulation outcome model (`synthetic-paired-outcome-model-v1`) accepts optional `root_ungraded_probability` (a whole root's grades in one arm are lost together), `failure_ungraded_probability` (extra loss for failed trials) and `deadline_trials` (the number of arm trials a parent deadline lets complete). A deadline cuts the fixed comparator into an `incomplete` run, ends a sequential run at a root boundary as `inconclusive` (`root_budget_not_reserved`) or inside a root as the engine's `incomplete` partial root, and keeps every sequential stop reached before it. The `completion` block names the loss pattern and counts unrun trials as ungraded. Models that omit the fields keep their model hash and simulated results; one sequential limitation line is reworded. The deadline counts arm trials, not seconds, and the report still runs no worker, model or grader and cannot authorize promotion.
