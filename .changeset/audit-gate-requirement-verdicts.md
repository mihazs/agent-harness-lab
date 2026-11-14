---
"agent-harness-lab": minor
---

Add `agent_harness_lab.diagnostics.audit_gate`, a preregistered requirement gate over the versioned `audit-summary-v1`. A caller-declared minimum review count, maximum missed-incident rate and optional minimum triage recall yield `requirement_met`, `requirement_not_met`, `inconclusive` or `invalid`; a limit passes or fails only when the whole 95% interval does, and pending reviews, an undefined recall or a straddling interval stay inconclusive. The result is observe-only and private, and authorizes no promotion or policy update. Part of #75.
