---
"agent-harness-lab": minor
---

Add `agent_harness_lab.diagnostics.judge.evaluate`, a library check of recorded pairwise diagnostic judge responses on held-out declared roots under a frozen human policy (`diagnostic-judge-evaluation-v1`). Every root is judged in both presentation orders, so a verdict that follows the position is an order disagreement, a single recorded order stays unchecked and an abstention is not a disagreement. Matched style, verbosity and wording controls must resolve to a tie, cited evidence is checked against the case's own spans (a fabricated or wrong-meaning reference is a failure, an unreviewed one stays existence-only), a judge that prefers or ties an arm an executable check flagged is rejected, self-reported confidence is ignored and an always-abstaining or self-consistent but mislabelled judge is rejected. It never calls a model and issues no diagnosis, beneficial claim or promotion authority. Part of #14.
