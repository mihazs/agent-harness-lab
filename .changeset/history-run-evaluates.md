---
"agent-harness-lab": patch
---

`ahl improve --history` no longer ends `blocked` with `no_outcome_evidence` before any model call when the history has findings and no verified outcome: an installing run goes on to propose and evaluate a candidate itself. Runs that keep the candidate a recommendation are unchanged.
