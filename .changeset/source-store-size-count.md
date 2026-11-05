---
"agent-harness-lab": patch
---

A source metadata cache with a byte limit no longer serializes the whole cache to check that limit before each commit. The new `atomic_json_size` helper counts the bytes `atomic_json` writes from the same encoder stream and stops once the limit is exceeded, so the check holds one chunk at a time instead of a string and a bytes copy of the cache. Ingest takes record identities from the records already validated on read instead of parsing each one again, keeps one dictionary per newly imported record, and checks metadata and graph references against the identity index without copying it into sets. The cache file format, the written bytes, the byte and record limits and every error message are unchanged.
