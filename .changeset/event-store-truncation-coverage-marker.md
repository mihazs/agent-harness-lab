---
"agent-harness-lab": patch
---

Mark a truncated `events.v2.jsonl` in-band. When the 64 MiB or 300 second store bound is reached, the file now ends with a content-free `capture_coverage` row per written case (`capture_complete=false`, `missing_reason=event_store_truncated`), reserved inside the byte bound. Skill activation receipts and other consumers of the file report `unknown` rather than `not_observed` for omitted events.
