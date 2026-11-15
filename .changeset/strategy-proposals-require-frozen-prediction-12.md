---
"agent-harness-lab": minor
---

Let a strategy run or comparison require a frozen prediction for every proposal. With a `PredictionGate`, the proposer returns a candidate and a `PredictionRecord`; the prediction is checked against the candidate hash, mechanism, baseline, bank and grader versions, protected task identities and the evidence allowlist, and frozen in the ledger before the candidate is charged, scored or dispatched on either route. A missing, mismatched or invalid prediction is a retained rejection that never runs, and the result lists the receipts and rejections (unknown, not empty, on a resumed run). Default-off and API-only: without a gate nothing changes, and the proposal request now also names its `strategy`.
