---
"agent-harness-lab": patch
---

`harness_inventory.join_loading` now counts a loading record once per `(case_id, event_id)`, so a capture read twice no longer inflates the counts. Repeats are reported as `duplicate_events` with a `duplicate_event_identity` warning, and `duplicate_event_conflict` when a repeat differs from the kept copy. `loading_evidence` is `none` unless a node has an exact attachment, and `attachment_shapes` is `none` when no attachment or hook output was counted.
