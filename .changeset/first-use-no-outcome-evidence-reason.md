---
"agent-harness-lab": minor
---

First use of `ahl improve --history` on a project with findings but no verified outcome no longer launches a preparation model call that cannot succeed. Without a verified executable-grader outcome and without a hypothesis source (`--focus` or `--from-audit`) the run ends `blocked` with the typed reason `no_outcome_evidence`, a remediation and zero model spend. Every receipt without a candidate whose analysis has findings now carries `findings_not_candidates` with a count per cause (`findings_not_actionable`), and a run whose preparation called the model while no trial was evaluated carries `spend_label: spent_without_evaluation` with the provider-reported spend marked unverified, also shown as `label=` in the outcome line. Adds the first-use scenario matrix under `tests/integration/improve/`; the static-convention candidate source stays with #332.
