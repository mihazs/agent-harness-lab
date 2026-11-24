---
"agent-harness-lab": patch
---

An installing `ahl improve` run now repeats a task the candidate lost (the baseline passed it, the candidate failed it) in both arms before that loss can end the run as `candidate_regressed`; a loss that does not repeat is not a regression, so a confirmed fix of the same attempt is still applied and otherwise the run ends `no_change`. The evidence is recorded as `regression_check`.
