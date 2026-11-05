---
"agent-harness-lab": patch
---

Treat a pytest case whose finite setup, call and teardown durations sum to a non-finite total as an invalid duration. The case now gets `duration_seconds: null` with the `invalid_duration` warning, as the source contract documents, instead of the whole test-results capture being refused with `invalid test duration`.
