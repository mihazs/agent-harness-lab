---
"agent-harness-lab": patch
---

Bring the synthetic equivalent of the real-project acceptance protocol up to the before/after guard. The opt-in `task test:isolated` rehearsal (and the default `task test`) now runs a worse candidate through the isolated check and requires the receipt to end `blocked` with reason `candidate_regressed` (exit 3), to carry the `investigation.json` beside `workflow.json` under the private state directory with the digest the receipt names, and to leave the real checkout unchanged; a candidate that is not worse must never be blocked and must leave no investigation record. The how-to maps the two new tests to the `before-after` step. Test and documentation only; no product behavior changes. Part of #808.
