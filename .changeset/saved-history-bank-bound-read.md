---
"agent-harness-lab": minor
---

Read a saved fixed paired history that is bound to a bank revision through `ahl analyze` and the `ahl improve` evidence stage with `--history-store` and `--history-run`. The route re-checks that the bank revision is still current and eligible (a revoked or changed bank fails explicitly), keeps `independence=unverified`, no promotion and no publication, and writes nothing to the history or its bank store: signature-verification scratch for an authenticated bank review now lives in the private analysis output, and the output, state and temporary-directory checks also refuse paths inside the declared bank store. Protected, derived, behavior-policy, cohort, sequential and responses-bound histories still fail before their validators run.
