---
"agent-harness-lab": minor
---

Read the `metadata.evidence` references of saved Cognee `recall` results by their pinned `kind`: a segment links its chunk and Data, a graph node its ID and a graph edge only its endpoints (the generated edge ID is in no export), all hashed under the declared dataset scope. References with the `supports_assertion` role are kept in a separate `supporting_node_refs` field, unknown kinds and roles are skipped and counted as `evidence_reference_unrecognized`, and a malformed reference or one naming another dataset refuses the file. A link is a candidate join, never proof. Part of #52.
