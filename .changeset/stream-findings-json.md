---
"agent-harness-lab": patch
---

Cut the peak memory of `ahl diagnose` by about half or more (46% at 20,000 events, 62% at 240,000 on synthetic traces) by streaming `findings.json` to disk instead of building the whole text first. The file is byte-identical, and a failed write no longer leaves a truncated file.
