---
"agent-harness-lab": minor
---

Add an offline `diagnostics.retrieval_leakage.evaluate` library check that rejects a lexical incident index holding a held-out probe's own declared root or a near copy of its text, marks which leaked incidents the probe would retrieve, and counts unknown-root probes, an empty index and paraphrase, project and time leakage as unverified instead of clean (part of #14).
