---
"agent-harness-lab": patch
---

Add a target-state application receipt: after a journaled adoption, `adopt_with_receipt` re-reads every installed path and mode, records created/updated/removed operations with the deployment id and rollback eligibility, and states that activation and behavioral effect are not established.
