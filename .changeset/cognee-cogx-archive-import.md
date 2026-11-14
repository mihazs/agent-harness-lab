---
"agent-harness-lab": minor
---

Import a Cognee COGX archive directory (`cognee.export(format="cogx")`) through the existing `cognee-export` source with `format: "cogx_archive"`. The reader opens only `manifest.json` and the four record files the pinned v1.6.5 writer emits, maps raw nodes, typed entities, chunk copies and facts onto the same dataset-scoped, hash-only claims a graph export gives, and keeps the archive's missing dataset ID as `caller_declared` or `unknown` lineage with cross-source joins disabled. An archive that carries `permissions.json` (account credentials from `include_permissions=True`) is refused before any file is opened, as are another producer or COGX release, a packed archive, symlinks, malformed lines and anything past the byte, row, field and entry bounds; manifest counts that disagree with the records are reported as `manifest_counts_mismatch`. The shape is read from code, not a real archive, so `compatibility` stays `unverified`.
