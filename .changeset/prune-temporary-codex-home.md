---
"agent-harness-lab": patch
---

After each Codex trial or preparation session, AHL deletes what Codex regenerates in the temporary `CODEX_HOME` it created (plugin downloads, caches, session databases) and keeps only the configuration, saving about 50 MB per session: a typical improve run drops from about 1.3 GB to about 0.3 GB. The user's own Codex home is never touched.
