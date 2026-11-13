---
"agent-harness-lab": minor
---

Link saved Cognee `recall` chunk, graph and code results to the graph-export claims they came from by hashing `metadata.data_id`, `metadata.chunk_id` and a code fact's pinned code-graph node ID (with its node type) under the declared dataset scope, keep names, paths and lines out of the claims, and refuse malformed provenance fields. Part of #52.
