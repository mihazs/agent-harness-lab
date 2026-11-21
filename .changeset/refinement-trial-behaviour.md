---
"agent-harness-lab": patch
---

In an installing `ahl improve` run, each failed development or regression trial in the refinement feedback carries `agent_final_message`, what the trial agent said it did (bounded, untrusted), so a refining proposer can see why its change missed a reproduced weakness.A measured installing run whose candidate fixed nothing and whose refining proposer then declines ends `no_change` (`evaluation_no_improvement`, `baseline_at_least_as_good`) instead of `inconclusive`.
