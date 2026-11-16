# Second-wave memory compatibility matrix

[Source contracts](source-contracts.md) · [Evidence reuse contract matrix](memory-reuse-contract-matrix.md) · [Command overview](command-reference.md)

This reference records what the public source of five second-wave memory systems shows
about reading their stored memories read-only: Graphiti and Zep, Supermemory, Letta,
LangMem with the LangGraph store, and Basic Memory. Every finding cites a file at a
pinned commit, so a reader can check it and a later release cannot silently change it.
It was checked on 2026-11-16 against the pins in the summary table.

It is a catalogue, not an adapter. No second-wave adapter exists yet, nothing here is
imported, run or contacted, and `ahl` reads none of these systems. A row marked
`verified` means the pinned source states the fact. It does not mean AHL supports the
system: a provider becomes supported only after independent mocked contract tests and a
real-format compatibility check, which are later slices of issue #56.
`tests/unit/docs/test_memory_compatibility_matrix.py` enforces the structure below.

## Status vocabulary

| Status | Meaning |
| --- | --- |
| `verified` | The pinned source states this fact, and the row cites the file. AHL has not exercised it. |
| `unsupported` | The pinned source offers no way to obtain this safely, or shows a default that would change the system being read. AHL records the dimension as unknown. |
| `requires_authorized_export` | The only read path is a live service, a database or a credential the operator controls. AHL can use only a file the operator exports and hands over. |
| `not_researched` | No source was read for this fact. Nothing is inferred. |

## Summary

| Id | System | Release | Commit | Licence | Safe read path | AHL adapter |
| --- | --- | --- | --- | --- | --- | --- |
| `graphiti` | [Graphiti core](https://github.com/getzep/graphiti) | v0.31.2 | `d72407dcedac911e1be9fc15b55e8c05a3efcc36` | Apache-2.0 | Operator-run listing of a graph database; no export file exists | none |
| `zep-cloud` | [Zep Cloud Python SDK](https://github.com/getzep/zep-python) | v4.1.0 | `305d8b3a03293355013d1e0e7f124337b7684a25` | Apache-2.0 | Operator-saved responses of the paged graph list calls | none |
| `supermemory` | [Supermemory](https://github.com/supermemoryai/supermemory) | server-v0.0.9 | `4036c7d4470d335022952e67da0c56048f341d23` | MIT | Operator-saved responses of the list tools; the server source is not public | none |
| `letta` | [Letta](https://github.com/letta-ai/letta) | 0.16.8 | `1131535716e8a31c9a437f8695e25ac98f203a24` | Apache-2.0 | Operator-saved agent file, blocks and messages only; archival memory is separate | none |
| `langgraph-store` | [LangGraph store](https://github.com/langchain-ai/langgraph) | 1.2.22 | `08a2016953789a4f579b4d36bd1967941ec62012` | MIT | Operator-made row dump of one store; no export format exists | none |
| `langmem` | [LangMem](https://github.com/langchain-ai/langmem) | 0.0.30 (no tag) | `8620b986af5b0c7593e6b7fbece2998690d22d15` | MIT | The same store row dump; LangMem has no storage of its own | none |
| `basic-memory` | [Basic Memory](https://github.com/basicmachines-co/basic-memory) | v1.0.0 | `c9bd187b83baba549d5cd16bacc180f8023d51c8` | AGPL-3.0-or-later | Operator-run `bm okf export` bundle of one project | none |

`graphiti` and `zep-cloud` together cover Graphiti/Zep, and `langgraph-store` and `langmem`
together cover LangMem and the LangGraph stores. LangMem has no release tag on GitHub, so it
is pinned to a commit whose `pyproject.toml` reads version 0.0.30, the version PyPI lists.
Zep's Community Edition and the Zep service itself are not covered; only the public SDK is.

## Findings per system

Each table has the same ten rows. The dimensions are the ones issue #56 names, with list
and history split because the answers differ.

### Graphiti core (`graphiti`)

| Dimension | Status | Finding |
| --- | --- | --- |
| Source identity and namespace | `verified` | Every node and edge has a `uuid` and a `group_id` partition ([nodes.py](https://github.com/getzep/graphiti/blob/d72407dcedac911e1be9fc15b55e8c05a3efcc36/graphiti_core/nodes.py), [edges.py](https://github.com/getzep/graphiti/blob/d72407dcedac911e1be9fc15b55e8c05a3efcc36/graphiti_core/edges.py)). The MCP server keeps each non-default group in its own graph ([graphiti_mcp_server.py](https://github.com/getzep/graphiti/blob/d72407dcedac911e1be9fc15b55e8c05a3efcc36/mcp_server/src/graphiti_mcp_server.py)), so a group id is a caller-chosen partition, not an account. |
| List and get | `verified` | `get_by_group_ids(driver, group_ids, limit, uuid_cursor)` and `get_by_uuid` list and fetch episodes, entities and edges in [nodes.py](https://github.com/getzep/graphiti/blob/d72407dcedac911e1be9fc15b55e8c05a3efcc36/graphiti_core/nodes.py) and [edges.py](https://github.com/getzep/graphiti/blob/d72407dcedac911e1be9fc15b55e8c05a3efcc36/graphiti_core/edges.py). The MCP `get_episodes` result keeps only uuid, name, content, created_at, source, source_description and group_id, so it drops `valid_at`, `entity_edges` and `episode_metadata`. No MCP tool lists every edge or entity without a search query. |
| History and revisions | `verified` | An edge records `valid_at`, `invalid_at`, `expired_at` and `reference_time`, and lists the episodes that produced it in `episodes` ([edges.py](https://github.com/getzep/graphiti/blob/d72407dcedac911e1be9fc15b55e8c05a3efcc36/graphiti_core/edges.py)). The end times let a record say that a fact stopped being true. An episode has no revision chain. |
| Official export or API | `requires_authorized_export` | The pinned tree has no export command or file format. Reading needs a running Neo4j, FalkorDB, Kuzu or Neptune database under [graphiti_core/driver](https://github.com/getzep/graphiti/tree/d72407dcedac911e1be9fc15b55e8c05a3efcc36/graphiti_core/driver), or the MCP or REST service over it. Both hold the operator's credentials, so AHL accepts only what the operator exports. |
| Read-only semantics | `unsupported` | The MCP server registers `add_memory`, `add_triplet`, `delete_entity_edge`, `delete_episode` and `clear_graph` beside its read tools, and neither the server nor its config schema has a read-only mode ([graphiti_mcp_server.py](https://github.com/getzep/graphiti/blob/d72407dcedac911e1be9fc15b55e8c05a3efcc36/mcp_server/src/graphiti_mcp_server.py)). The REST service has ingest, delete and clear routes ([ingest.py](https://github.com/getzep/graphiti/blob/d72407dcedac911e1be9fc15b55e8c05a3efcc36/server/graph_service/routers/ingest.py)). A read allowlist would have to be fixed by tool name. |
| Privacy scope | `verified` | `group_id` is on every record. `get_episodes` returns an empty list when no group id is given and no default is configured ([graphiti_mcp_server.py](https://github.com/getzep/graphiti/blob/d72407dcedac911e1be9fc15b55e8c05a3efcc36/mcp_server/src/graphiti_mcp_server.py)). Equal group ids in two databases name two partitions, so an adapter must key on the database identity plus the group id. |
| Lifecycle and TTL | `verified` | Invalidation is recorded as `invalid_at` and `expired_at` on the edge. The pinned models have no TTL field ([edges.py](https://github.com/getzep/graphiti/blob/d72407dcedac911e1be9fc15b55e8c05a3efcc36/graphiti_core/edges.py)). |
| Cost and inference side effects | `verified` | `search` embeds the query text unless a vector is supplied ([search.py](https://github.com/getzep/graphiti/blob/d72407dcedac911e1be9fc15b55e8c05a3efcc36/graphiti_core/search/search.py)). `build_communities` is documented as expensive and `summarize_saga` generates a summary. Listing by group id or uuid calls neither. |
| Freshness | `unsupported` | Paging compares `uuid` against a cursor (`uuid_cursor`), and there is no snapshot token, so a paged read is not atomic against concurrent ingestion. |
| Completeness | `unsupported` | Listing returns no total or manifest. MCP `get_episodes` stops at `max_episodes` per group. |

Candidate read tools for an optional connector, never enabled by this page: `get_episodes`,
`get_entity_edge` and `get_episode_entities`. The two search tools embed the query and are
excluded. Names and schemas must be re-verified at the pin before any use.

### Zep Cloud SDK (`zep-cloud`)

| Dimension | Status | Finding |
| --- | --- | --- |
| Source identity and namespace | `verified` | Episodes and edges carry a uuid with `graph_uuid`, `user_uuid` and `thread_uuid`, and edges carry `source_node_uuid`, `target_node_uuid`, `episode_uuids` and `scope` ([edge.py](https://github.com/getzep/zep-python/blob/305d8b3a03293355013d1e0e7f124337b7684a25/src/zep_cloud/types/edge.py), [episode.py](https://github.com/getzep/zep-python/blob/305d8b3a03293355013d1e0e7f124337b7684a25/src/zep_cloud/types/episode.py)). |
| List and get | `verified` | `graph.episode`, `graph.edge` and `graph.node` each have `list` and `get`, and nodes add `list_neighbors` ([episode client](https://github.com/getzep/zep-python/blob/305d8b3a03293355013d1e0e7f124337b7684a25/src/zep_cloud/graph/episode/client.py)). `list` takes `page_size`, `cursor`, `order_by` and `order`. |
| History and revisions | `verified` | Edges record `valid_at`, `invalid_at` and `expired_at` ([edge.py](https://github.com/getzep/zep-python/blob/305d8b3a03293355013d1e0e7f124337b7684a25/src/zep_cloud/types/edge.py)). The pinned graph clients have no history listing, and an episode has a `processed` flag but no revision chain. |
| Official export or API | `requires_authorized_export` | Hosted API reached with an `Api-Key` credential ([client_wrapper.py](https://github.com/getzep/zep-python/blob/305d8b3a03293355013d1e0e7f124337b7684a25/src/zep_cloud/core/client_wrapper.py)). The graph clients have no bulk export; the only export client in the tree is for agent skills. |
| Read-only semantics | `unsupported` | The same clients expose `add`, `update` and `delete`, and the SDK has no read-only mode. Only an operator-saved list or get response is a read AHL can use. |
| Privacy scope | `verified` | Graph, user and thread uuids are on each record ([episode.py](https://github.com/getzep/zep-python/blob/305d8b3a03293355013d1e0e7f124337b7684a25/src/zep_cloud/types/episode.py)). A reader must key on the graph uuid. |
| Lifecycle and TTL | `verified` | `valid_at`, `invalid_at` and `expired_at` on edges, and `processed` on episodes ([edge.py](https://github.com/getzep/zep-python/blob/305d8b3a03293355013d1e0e7f124337b7684a25/src/zep_cloud/types/edge.py), [episode.py](https://github.com/getzep/zep-python/blob/305d8b3a03293355013d1e0e7f124337b7684a25/src/zep_cloud/types/episode.py)). The pinned types have no TTL field. |
| Cost and inference side effects | `not_researched` | The SDK does not show what the service bills or computes for a list call. |
| Freshness | `unsupported` | `list` has no snapshot token, and `processed` only reports ingestion state per episode ([episode client](https://github.com/getzep/zep-python/blob/305d8b3a03293355013d1e0e7f124337b7684a25/src/zep_cloud/graph/episode/client.py)). |
| Completeness | `verified` | A page carries `items`, `next_cursor` and an optional `total_size` ([episode_page.py](https://github.com/getzep/zep-python/blob/305d8b3a03293355013d1e0e7f124337b7684a25/src/zep_cloud/types/episode_page.py)). A missing `total_size` is an unknown total, not zero. |

### Supermemory (`supermemory`)

| Dimension | Status | Finding |
| --- | --- | --- |
| Source identity and namespace | `verified` | Memory entries and documents have ids. A call resolves its space from an explicit `namespace`, then the account's active space, then `sm_project_default` ([README](https://github.com/supermemoryai/supermemory/blob/4036c7d4470d335022952e67da0c56048f341d23/apps/mcp/README.md)). The default name is the same for every account, so an adapter must key on account plus space. |
| List and get | `verified` | `list_memories` (page size at most 50), `list_documents` and `get_document` are registered read tools ([list-memories.ts](https://github.com/supermemoryai/supermemory/blob/4036c7d4470d335022952e67da0c56048f341d23/apps/mcp/src/server/tools/list-memories.ts)). Entries carry `version`, `isLatest`, `isForgotten`, `isStatic` and `isInference`. |
| History and revisions | `unsupported` | The MCP drops forgotten and non-latest entries after paging, and its mapper omits `parentMemoryId` and `rootMemoryId` ([client/index.ts](https://github.com/supermemoryai/supermemory/blob/4036c7d4470d335022952e67da0c56048f341d23/apps/mcp/src/server/client/index.ts)). Version chains cannot be rebuilt from it. |
| Official export or API | `requires_authorized_export` | The hosted MCP validates an OAuth token on every request. The public tree has no export command, and the self-hosted server source is not in it ([local-vs-enterprise.mdx](https://github.com/supermemoryai/supermemory/blob/4036c7d4470d335022952e67da0c56048f341d23/apps/docs/self-hosting/local-vs-enterprise.mdx)). |
| Read-only semantics | `verified` | The read tools declare `readOnlyHint` and the writers do not: `add_memory` and `forget_memory` are destructive ([annotations.ts](https://github.com/supermemoryai/supermemory/blob/4036c7d4470d335022952e67da0c56048f341d23/apps/mcp/src/server/tools/annotations.ts)). These hints are the server's own claim, so a connector would pin tool names and schemas and not trust the hint. |
| Privacy scope | `verified` | Space state is keyed by organization plus user, and the token is checked per request ([README](https://github.com/supermemoryai/supermemory/blob/4036c7d4470d335022952e67da0c56048f341d23/apps/mcp/README.md)). |
| Lifecycle and TTL | `verified` | `isForgotten`, `isLatest` and `version` mark lifecycle, and the mapped entry has no TTL field ([client/index.ts](https://github.com/supermemoryai/supermemory/blob/4036c7d4470d335022952e67da0c56048f341d23/apps/mcp/src/server/client/index.ts)). |
| Cost and inference side effects | `not_researched` | `search_memory` is semantic recall and `isInference` marks inferred entries. What reads cost the account was not researched, so `search_memory` stays off any allowlist. |
| Freshness | `unsupported` | Pages are numbered and sorted by `createdAt` descending, so a new memory shifts every later page. There is no snapshot token. |
| Completeness | `unsupported` | `list_memories` filters after paging but returns the unfiltered `pagination` block, so the returned count and `totalItems` can disagree ([list-memories.ts](https://github.com/supermemoryai/supermemory/blob/4036c7d4470d335022952e67da0c56048f341d23/apps/mcp/src/server/tools/list-memories.ts)). |

Candidate read tools for an optional connector, never enabled by this page: `list_memories`,
`list_documents`, `get_document` and `list_spaces`.

### Letta (`letta`)

| Dimension | Status | Finding |
| --- | --- | --- |
| Source identity and namespace | `unsupported` | Live records have ids, but the agent file remaps every id to a file-local counter such as `block-<n>` and `agent-<n>` ([agent_serialization_manager.py](https://github.com/letta-ai/letta/blob/1131535716e8a31c9a437f8695e25ac98f203a24/letta/services/agent_serialization_manager.py)). An id from one export is not the same source as the same id in another, so identity must come from the operator's declaration and content. |
| List and get | `verified` | `GET /v1/blocks/`, `GET /v1/blocks/{block_id}`, `GET /v1/agents/{agent_id}/core-memory/blocks` and the paged `GET /v1/agents/{agent_id}/archival-memory` (`after`, `before`, default `limit` of 100) ([agents.py](https://github.com/letta-ai/letta/blob/1131535716e8a31c9a437f8695e25ac98f203a24/letta/server/rest_api/routers/v1/agents.py), [blocks.py](https://github.com/letta-ai/letta/blob/1131535716e8a31c9a437f8695e25ac98f203a24/letta/server/rest_api/routers/v1/blocks.py)). |
| History and revisions | `not_researched` | `Block` and the agent file's `BlockSchema` have no version or revision field and `blocks.py` has no history route ([block.py](https://github.com/letta-ai/letta/blob/1131535716e8a31c9a437f8695e25ac98f203a24/letta/schemas/block.py)). A memory repository schema ([memory_repo.py](https://github.com/letta-ai/letta/blob/1131535716e8a31c9a437f8695e25ac98f203a24/letta/schemas/memory_repo.py)) and a git HTTP router exist and were not researched. |
| Official export or API | `requires_authorized_export` | `GET /v1/agents/{agent_id}/export` returns an `AgentFileSchema` with agents, groups, blocks, files, sources, tools, MCP servers, skills and messages, and `scrub_messages` drops messages. It needs a running server. The file also carries tool source and MCP `stdio_config`, which are executable and may hold credentials, so AHL would read blocks and messages only and never run or keep the rest ([agent_file.py](https://github.com/letta-ai/letta/blob/1131535716e8a31c9a437f8695e25ac98f203a24/letta/schemas/agent_file.py)). |
| Read-only semantics | `not_researched` | The export route is declared `GET`, and `POST /v1/agents/import` and the mutating routes share the server. Whether a server-side read changes state, such as access times, was not researched. |
| Privacy scope | `verified` | Requests act as an actor taken from headers, passages carry `organization_id` and `archive_id`, and a block can belong to several agents or groups through `shared_block_ids` ([passage.py](https://github.com/letta-ai/letta/blob/1131535716e8a31c9a437f8695e25ac98f203a24/letta/schemas/passage.py), [agent_file.py](https://github.com/letta-ai/letta/blob/1131535716e8a31c9a437f8695e25ac98f203a24/letta/schemas/agent_file.py)). One shared block in three agent files is one source, not three. |
| Lifecycle and TTL | `verified` | A passage has `is_deleted` and `created_at`, and a block has `limit` and `read_only`. The pinned schemas have no TTL field ([passage.py](https://github.com/letta-ai/letta/blob/1131535716e8a31c9a437f8695e25ac98f203a24/letta/schemas/passage.py), [block.py](https://github.com/letta-ai/letta/blob/1131535716e8a31c9a437f8695e25ac98f203a24/letta/schemas/block.py)). |
| Cost and inference side effects | `not_researched` | `list_passages` takes a `search` text parameter and whether it embeds the query was not researched. Passage rows carry `embedding` vectors, which make listings large. |
| Freshness | `unsupported` | Passage paging uses `after` and `before` ids with no snapshot token. |
| Completeness | `unsupported` | `AgentFileSchema` has no passage field, so an agent file omits archival memory and cannot be a complete memory export ([agent_file.py](https://github.com/letta-ai/letta/blob/1131535716e8a31c9a437f8695e25ac98f203a24/letta/schemas/agent_file.py)). |

### LangGraph store (`langgraph-store`)

| Dimension | Status | Finding |
| --- | --- | --- |
| Source identity and namespace | `verified` | An `Item` is a `value` dictionary with a `key` unique within a `namespace` tuple, plus `created_at` and `updated_at` ([store/base/__init__.py](https://github.com/langchain-ai/langgraph/blob/08a2016953789a4f579b4d36bd1967941ec62012/libs/checkpoint/langgraph/store/base/__init__.py)). The namespace is the only scope field. |
| List and get | `verified` | `get`, `search` (`namespace_prefix`, `filter`, `limit`, `offset`) and `list_namespaces` (prefix, suffix, `max_depth`, `limit`, `offset`) on a store object inside the operator's process ([store/base/__init__.py](https://github.com/langchain-ai/langgraph/blob/08a2016953789a4f579b4d36bd1967941ec62012/libs/checkpoint/langgraph/store/base/__init__.py)). |
| History and revisions | `unsupported` | An item has no revision, supersession or provenance field. |
| Official export or API | `unsupported` | The store is a Python library over the operator's own database ([in-memory](https://github.com/langchain-ai/langgraph/blob/08a2016953789a4f579b4d36bd1967941ec62012/libs/checkpoint/langgraph/store/memory/__init__.py), [SQLite](https://github.com/langchain-ai/langgraph/blob/08a2016953789a4f579b4d36bd1967941ec62012/libs/checkpoint-sqlite/langgraph/store/sqlite/base.py), [Postgres](https://github.com/langchain-ai/langgraph/blob/08a2016953789a4f579b4d36bd1967941ec62012/libs/checkpoint-postgres/langgraph/store/postgres/base.py)). It defines no export format. The only path is a row dump the operator writes. |
| Read-only semantics | `unsupported` | `get` and `search` default `refresh_ttl` to true, and an item's timer refreshes on reads, so with a TTL configured an ordinary read postpones expiry and changes what a later read finds ([store/base/__init__.py](https://github.com/langchain-ai/langgraph/blob/08a2016953789a4f579b4d36bd1967941ec62012/libs/checkpoint/langgraph/store/base/__init__.py)). A reader must pass `refresh_ttl=False`. |
| Privacy scope | `verified` | A search matches a namespace prefix, so a short prefix reaches every namespace beneath it, including other users' ([store/base/__init__.py](https://github.com/langchain-ai/langgraph/blob/08a2016953789a4f579b4d36bd1967941ec62012/libs/checkpoint/langgraph/store/base/__init__.py)). An adapter must read exact namespaces, never a bare prefix. |
| Lifecycle and TTL | `verified` | `PutOp.ttl` is in minutes and counts from last access, when the store supports it ([store/base/__init__.py](https://github.com/langchain-ai/langgraph/blob/08a2016953789a4f579b4d36bd1967941ec62012/libs/checkpoint/langgraph/store/base/__init__.py)). |
| Cost and inference side effects | `verified` | Natural-language `query` search depends on the store implementation, and the in-memory store sends the text to the `embed` function of its index configuration ([store/memory/__init__.py](https://github.com/langchain-ai/langgraph/blob/08a2016953789a4f579b4d36bd1967941ec62012/libs/checkpoint/langgraph/store/memory/__init__.py)). |
| Freshness | `unsupported` | `limit` and `offset` paging, no snapshot token. |
| Completeness | `unsupported` | Defaults are `limit=10` for `search` and `limit=100` for `list_namespaces`, and no call returns a total. |

### LangMem (`langmem`)

| Dimension | Status | Finding |
| --- | --- | --- |
| Source identity and namespace | `verified` | Memories are LangGraph store items under the default namespace `("memories", "{langgraph_user_id}")`, whose placeholder is filled from the run's `configurable` mapping ([knowledge/extraction.py](https://github.com/langchain-ai/langmem/blob/8620b986af5b0c7593e6b7fbece2998690d22d15/src/langmem/knowledge/extraction.py), [utils.py](https://github.com/langchain-ai/langmem/blob/8620b986af5b0c7593e6b7fbece2998690d22d15/src/langmem/utils.py)). A stored row keeps only the filled namespace. |
| List and get | `verified` | The memory store manager wraps the store's `get` and `search`, and each accepts `refresh_ttl` ([extraction.py](https://github.com/langchain-ai/langmem/blob/8620b986af5b0c7593e6b7fbece2998690d22d15/src/langmem/knowledge/extraction.py)). |
| History and revisions | `unsupported` | The stored value is `kind` plus `content` and nothing else, with no revision field. |
| Official export or API | `unsupported` | The source tree has no storage or export module, so a LangMem export is the LangGraph store row dump above. |
| Read-only semantics | `unsupported` | It inherits the default TTL refresh on reads of the store. The manager's main operations call a model and write back, and they are not a read path. |
| Privacy scope | `verified` | Scope is whatever the runtime configuration filled into the template, and each manager has its own template. The namespace tuple is the only evidence a reader has ([utils.py](https://github.com/langchain-ai/langmem/blob/8620b986af5b0c7593e6b7fbece2998690d22d15/src/langmem/utils.py)). |
| Lifecycle and TTL | `verified` | LangMem adds none and passes `refresh_ttl` through to the store ([extraction.py](https://github.com/langchain-ai/langmem/blob/8620b986af5b0c7593e6b7fbece2998690d22d15/src/langmem/knowledge/extraction.py)). |
| Cost and inference side effects | `verified` | Extraction and consolidation run a language model, and searching with a query uses the store's embedder ([extraction.py](https://github.com/langchain-ai/langmem/blob/8620b986af5b0c7593e6b7fbece2998690d22d15/src/langmem/knowledge/extraction.py)). A stored memory is therefore model output and stays a claim, never evidence of task correctness. |
| Freshness | `unsupported` | Inherited from the store: paging by offset with no snapshot token. |
| Completeness | `unsupported` | Inherited from the store: default limits and no total. |

### Basic Memory (`basic-memory`)

| Dimension | Status | Finding |
| --- | --- | --- |
| Source identity and namespace | `verified` | Notes are Markdown files with frontmatter inside a configured project, found by path, title and permalink ([okf-export(1).md](https://github.com/basicmachines-co/basic-memory/blob/c9bd187b83baba549d5cd16bacc180f8023d51c8/src/basic_memory/man/man1/okf-export(1).md)). The project is the scope. |
| List and get | `verified` | The files themselves, and MCP read, list, find, context and recent-activity tools ([tools directory](https://github.com/basicmachines-co/basic-memory/tree/c9bd187b83baba549d5cd16bacc180f8023d51c8/src/basic_memory/mcp/tools)). Write, edit, delete and move tools live in the same package. |
| History and revisions | `verified` | The export's root `log.md` records accepted journal history under ISO date headings, best effort. File contents may lag the journal, and a database older than the journal gives an empty history ([okf-export(1).md](https://github.com/basicmachines-co/basic-memory/blob/c9bd187b83baba549d5cd16bacc180f8023d51c8/src/basic_memory/man/man1/okf-export(1).md)). |
| Official export or API | `verified` | `bm okf export DESTINATION --project PROJECT [--replace] [--json]` writes an OKF v0.2-compatible bundle outside the project ([okf.py](https://github.com/basicmachines-co/basic-memory/blob/c9bd187b83baba549d5cd16bacc180f8023d51c8/src/basic_memory/cli/commands/okf.py)). Typed relations survive in a `bm.okf_export` frontmatter key. Cloud projects must be pulled locally first, and the bundle is a file the operator hands over. |
| Read-only semantics | `verified` | Source files remain unchanged, a source change during export fails the export, and an older database is not migrated by it ([okf-export(1).md](https://github.com/basicmachines-co/basic-memory/blob/c9bd187b83baba549d5cd16bacc180f8023d51c8/src/basic_memory/man/man1/okf-export(1).md)). It is meant for a project nobody is editing. |
| Privacy scope | `verified` | One configured project per export, following the project's ignore rules, with symlinks not exported ([okf-export(1).md](https://github.com/basicmachines-co/basic-memory/blob/c9bd187b83baba549d5cd16bacc180f8023d51c8/src/basic_memory/man/man1/okf-export(1).md)). The operator picks the project, so private notes in it travel with it. |
| Lifecycle and TTL | `not_researched` | The export document names no expiry. Whether notes can expire was not researched. |
| Cost and inference side effects | `verified` | The export module's module-level imports are configuration, rendering, validation, a permalink helper and the standard library, with no model or embedding client ([export.py](https://github.com/basicmachines-co/basic-memory/blob/c9bd187b83baba549d5cd16bacc180f8023d51c8/src/basic_memory/okf/export.py)). |
| Freshness | `unsupported` | The export document describes no timestamp or project revision for the bundle itself, and the log may lag the files. Unchanged state gives identical bytes, which detects change but not age. |
| Completeness | `verified` | Every non-ignored regular file is exported, assets keep their paths, and notes named `.markdown` or `.MD` must be renamed before export. Unresolved or ambiguous links stay as they are. This is upstream's statement ([okf-export(1).md](https://github.com/basicmachines-co/basic-memory/blob/c9bd187b83baba549d5cd16bacc180f8023d51c8/src/basic_memory/man/man1/okf-export(1).md)), and AHL has not checked it. |

Basic Memory is AGPL-3.0-or-later. AHL consumes only the data of a bundle the operator
produced. It copies no Basic Memory code, imports no Basic Memory package and adds no
dependency on it. AHL's existing [ai-memory OKF reader](source-contracts.md#ai-memory-okf-project-exports)
targets another producer's OKF v0.2 documentation, and whether it reads a Basic Memory
bundle unchanged has not been tested. That is the real-format compatibility check a later
slice owes before this system can be called supported.

## Surveyed but not researched

Issue #56 also names these systems. No source was read for them, so no capability is
claimed and none has a section above.

| System | Status |
| --- | --- |
| OpenViking | `not_researched` |
| mcp-memory-service | `not_researched` |
| A-MEM | `not_researched` |
| agentmemory | `not_researched` |
| Platform memory systems that publish a stable supported export | `not_researched` |

A system leaves this table only with a pinned source read and a section like those above.

## Rules for every adapter

These hold before any adapter is written, and the adapter slices of issue #56 must test them.

1. A memory block, memory entry, graph edge, retrieval hit or generated summary is a claim about what a system stored. It is never verified task correctness and never an independent task outcome.
2. A graph entity or fact repeated across documents or episodes is one source claim, not several independent outcomes. A block shared by several agents is one source.
3. Read calls that embed a query, run a model, bill the account or refresh a TTL stay off any read allowlist unless the operator authorizes them separately. Reads of a TTL store pass `refresh_ttl=False`.
4. Identity is the provider instance plus the account, graph, project or space plus the namespace and key. A namespace string alone never identifies a source, and overlapping namespaces in different scopes stay separate.
5. An unknown total, snapshot, history or scope is recorded as unknown. It never becomes zero, complete or independent.
6. AHL reads only files the operator exports and hands over. It holds no credentials for these systems, calls no mutating tool, runs no model, and never executes content of an export, such as tool source in an agent file.

## What remains of issue #56

| Acceptance item | State |
| --- | --- |
| A published matrix covers every named candidate and states what cannot be read safely | Done by this page for the five named systems. The four other surveyed systems are `not_researched`. |
| Versioned offline fixtures keep source revisions and graph or namespace identity, and a missing capability fails explicitly | Not started. It needs the generic export fixture adapter. |
| Memory blocks, graph edges and retrieval hits never become verified task correctness | Stated as rule 1. Enforcement belongs to the adapter slice. |
| Multi-user and multi-project scopes do not leak when namespaces overlap | Stated as rule 4. Enforcement belongs to the adapter slice. |
| No mandatory provider SDK, network access or model download in the base install | Guarded now: the unit test fails if a provider package appears in any dependency list of `pyproject.toml`. |
