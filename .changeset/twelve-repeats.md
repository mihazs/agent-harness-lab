---
"agent-harness-lab": patch
---

The confirmation of an installing `ahl improve` run takes up to twelve fresh repeats per fixed task (was six), and the run has 80 trials (was 64): agents are noisy, and six repeats could not separate a candidate that always passed from a baseline that passed half the time. When a screening run prunes the claims the baseline did not show, the pruned candidate is now judged on the first attempt's frozen, validated tasks and controls instead of a newly built evaluation, so a clean first attempt can no longer end `evaluation_failed` over a control the second author broke; the receipt records `evaluation_reused`. A proposer's instruction plan whose runtime profile names only `task_cwd` is completed from the controller's pinned runtime, and an unusable plan beside a root-only instruction change is ignored instead of blocking the run.
