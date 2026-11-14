---
"agent-harness-lab": minor
---

Withhold imported free-text activity names from the model-facing role views: a name that is not a short label appears as a `withheld-activity-<digest>` placeholder in every role's `analysis-summary.json` (counts and structure unchanged, `limitations` says how many were replaced), so instruction-shaped text in a normalized, ai-memory or descriptive native capture is data, never a prompt. Add a synthetic injection-canary sweep over every imported capture adapter and the role instruction strings. Part of #58 (O144); the full native chain, semantic derivation grouping and the diagnostic-utility study remain.
