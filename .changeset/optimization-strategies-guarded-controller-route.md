---
"agent-harness-lab": minor
---

Route registered optimization strategies through the guarded controller route (part of #12): `run_strategy` and `compare_strategies` accept a `HistorySearchContext` as `controller` (with `evaluate` and `certify` left `None`), so the no-op, single-revision and reflective GEPA-inspired built-ins execute and verify every candidate and a fresh development confirmation through the actual controller instead of trusting callback `safe` and metric values. Comparison rows then report the durable stage charges, the invalid and duplicate attempts as rejections and `development_recommended_for_review` finalists only after a confirmation that passed; the report is labelled `controller_verified_development_only`, nothing is certified and no advantage is claimed. A raw callback beside a controller, a callback route without both callbacks and a deadline without a controller are refused before any state exists. See `docs/optimization-strategies.md`.
