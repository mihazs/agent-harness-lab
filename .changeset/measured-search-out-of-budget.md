---
"agent-harness-lab": patch
---

An installing run that runs out of budget after measured attempts that showed no gain now ends `no_change` / `evaluation_no_improvement` (with `budget_exhausted_during_search`), and the verified-correction lane can run on those attempts. A run that ran out while a gain was pending (a confirmation, or a fixed task with nothing lost) still ends `budget_exhausted`.
