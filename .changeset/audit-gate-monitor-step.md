---
"agent-harness-lab": minor
---

Add `agent_harness_lab.promotion.monitor.triage_audit_gate`, which recomputes a private triage-audit observation, summarizes it with an optional pre-label decision-log report and judges it against a preregistered `audit-gate-requirement-v1`. Monitoring, the versioned summary and the gate now read one policy, model and receipt identity; a foreign policy or an invalid requirement is refused and the result is observe-only, authorizes no promotion and updates no policy. Part of #75.
