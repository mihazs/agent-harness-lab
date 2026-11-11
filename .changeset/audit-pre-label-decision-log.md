---
"agent-harness-lab": minor
---

Add `diagnostics.audit_decisions.check`, which verifies a hash-chained log of audit budget changes and adaptive sampling decisions against a sampling receipt and marks design-based estimates unsupported when a decision is logged at or after the first label (part of #75).
