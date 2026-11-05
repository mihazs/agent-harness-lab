---
"agent-harness-lab": patch
---

Native Codex and Claude captures use less peak memory. Decoded rows are released as soon as they are correlated instead of staying alive next to the normalized events, discovery drops its merge and conflict tables before building snapshots, pinned `collect` lets analysis free each event once it has been consumed, and the Claude projection sorts in place. Events still become visible only after the capture is re-verified. Records, summaries, digests, snapshots and journal bytes are unchanged.
