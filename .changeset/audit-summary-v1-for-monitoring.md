---
"agent-harness-lab": minor
---

Add `agent_harness_lab.diagnostics.audit_summary`, a versioned `audit-summary-v1` record that compacts a triage audit monitor observation and an optional pre-label decision-log report for monitoring and the triage gate, plus a `check` that rejects an altered summary or one bound to a different triage policy or model. A decision at or after the first label invalidates the summary's estimates. The summary is observe-only and private, and authorizes no promotion or policy update. Part of #75.
