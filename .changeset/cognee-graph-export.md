---
"agent-harness-lab": minor
---

Add an offline `local_file/cognee-export/v1` source for one selected Cognee v1.6.5 graph export of one dataset (`GraphSnapshot.save()` JSON or the `format="json"` nodes/edges file). Documents, chunks, entities, other nodes and edges become separate hash-only claims namespaced by instance and dataset, each with an origin-document status; an export naming another dataset is refused, and a file without a dataset ID gets unknown lineage with cross-source joins disabled. Nothing imports Cognee, runs recall or inference, opens a store or mutates the graph, and the shape stays labelled unverified.
