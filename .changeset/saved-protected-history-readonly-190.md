---
"agent-harness-lab": minor
---

Read a saved fixed paired history that carries managed protected access or a controlled derivation binding through `ahl analyze` and the `ahl improve` evidence stage with `--history-store` and `--history-run` (part of #190). The route re-checks the frozen receipt against the existing protected-access ledger under its existing dependency lock, never creating that lock or the ledger: a missing lock or ledger, a reopened or removed reservation or a changed derivation link fails explicitly and the store stays byte-identical. Results keep `qualification=unverified`, `independence=unverified`, no promotion and no publication. Behavior-policy, cohort, sequential and responses-bound histories still fail before their validators run.
