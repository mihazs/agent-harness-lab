---
"agent-harness-lab": minor
---

Add a `diagnostic-corpus` input profile to `ahl benchmark`. It reads a digest-checked `CorpusStore.export_development` manifest, keeps the corpus fitting/development split and declared root groups, and never re-splits or admits protected partitions. Exposure-retired protected items must carry the export's retirement reference and are counted by declared partition in the report. Every report states label provenance counts; default JSONL rows are labeled `adapter_smoke` and never count as reviewed. Corpus reports are private and descriptive only, and they grant no gold-label, certification or classifier-selection authority.
