---
"agent-harness-lab": minor
---

Report whether corpus reviews carry stored key approvals. `CorpusStore.snapshot()` items and every `export_development` row now carry `review_approval` (`no_review_events`, `operator_attested`, `partial_key_approvals_recorded` or `key_approvals_recorded`), and `ahl benchmark --profile diagnostic-corpus` reports `review_approval_counts` per split with `review_approval_verification: not_performed`. The status counts approval records only: it does not re-run the signature check (`gold_label_policy` does), and it changes no label provenance, gold-label, certification, publication or classifier-selection authority. `development_export` rejects a row whose status is missing or outside the frozen states, so exports made before this change must be regenerated. Part of #73.
