---
"agent-harness-lab": minor
---

Add an opt-in private memory store (part of #629). `ahl memory init|status|verify|reindex|gc` manages a local directory whose authoritative objects are immutable, hash-addressed and re-verified on every read, with a derived SQLite index (versioned forward-only schema, enforced enums and foreign keys, per-project row ceilings) that `reindex` rebuilds from manifests. The store refuses relative, symlinked, checkout-internal and overlapping locations, opens a newer schema read-only, allows one writer at a time, and `gc` plans before it removes anything and never removes referenced objects. Nothing populates or reads the store yet, and the existing `HistoryStore` is untouched. See `docs/memory-store.md`.
