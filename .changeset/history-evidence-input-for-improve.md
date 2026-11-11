---
"agent-harness-lab": minor
---

Add a read-only `--history [--since DATE|first] [--source native]` evidence input to `ahl improve` and `ahl skills invoke` that reuses the existing session collection, reports captured and unknown evidence, and blocks unsupported or empty requests with typed reasons and exact next steps. Run directories now honor `AHL_STATE_HOME` (part of #663).
