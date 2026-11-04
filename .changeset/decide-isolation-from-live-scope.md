---
"agent-harness-lab": minor
---

Derive the isolation element of the recommendation-only `promotion.policy.decide` API from a live qualified implementation-prerequisite scope (`prerequisite_scope`) instead of a caller boolean. A scope that is absent, lost, oversized, copied, expired or not fully qualified reports `isolation: unknown` and blocks. The legacy `isolation_verified` keyword is now declaration-only: it is recorded under `declared_prerequisites` and can no longer satisfy isolation, so decisions only become stricter. Other certification prerequisites, automatic promotion and adoption authority are unchanged.
