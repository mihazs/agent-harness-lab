---
"agent-harness-lab": patch
---

When an installing run has already measured an attempt and a later evaluation author overruns its time box, the run ends `no_change` on the measured result (with `later_construction_failed`), and the verified-correction lane can still run, instead of `budget_exhausted`.
