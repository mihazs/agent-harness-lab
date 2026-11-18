# Offline Graphiti episode response projection

[Compatibility matrix](memory-compatibility-matrix.md) · [Source contracts](source-contracts.md)

`GraphitiEpisodes` reads one caller-supplied JSON object shaped like the pinned Graphiti v0.31.2
MCP `get_episodes` response at commit `d72407dcedac911e1be9fc15b55e8c05a3efcc36`. The
[upstream response construction](https://github.com/getzep/graphiti/blob/d72407dcedac911e1be9fc15b55e8c05a3efcc36/mcp_server/src/graphiti_mcp_server.py#L849-L867)
selects UUID, name, content, creation time, source, source description and group. The
[response type](https://github.com/getzep/graphiti/blob/d72407dcedac911e1be9fc15b55e8c05a3efcc36/mcp_server/src/models/response_types.py#L36-L38)
contains a message and an episode list. This connector accepts only that shape, not arbitrary provider
exports, graph databases, MCP messages or other versions. It never starts a provider, uses credentials,
performs network access or refreshes memory.

Use an explicitly authorized local-export manifest whose enabled source is `local_file`, provider
`graphiti-mcp-episodes`, version `v0.31.2`, source-declared identity and no authentication. The caller
constructs `GraphitiEpisodes(manifest, project=..., group=...)`; its source must use
`digest(('graphiti.project', project))` and `digest(('graphiti.group', group))` for project and scope.
Each episode must declare that exact group. Graphiti's group is a partition, not account or project
proof; both the selected project and group are caller declarations, not authenticated identities.

The reader bounds input to 2 MiB and 5,000 episodes, rejects duplicate identities and malformed or
foreign rows, and rechecks the input and manifest after reading. Each episode becomes a private
contextual assertion. Its revision fingerprints the selected response row; Graphiti did not supply a
native revision. The row hash can expose equality or low-entropy guesses, and journals are not public.
An empty response and a short result do not establish complete capture; upstream does not expose a
complete snapshot here. Episode content, names and descriptions are not stored as text. Missing graph
nodes, edges, episode links, source changes and provider lifecycle details remain unobserved.

The projection is experimental: its fixture mirrors the pinned public response construction, but no
real deployment or independently captured provider response has been checked. It does not imply full
Graphiti support or verified task outcomes.

## Entity-edge declarations

`GraphitiEdges` reads an explicitly supplied JSON array shaped like pinned Graphiti
[`EntityEdge` objects](https://github.com/getzep/graphiti/blob/d72407dcedac911e1be9fc15b55e8c05a3efcc36/graphiti_core/edges.py#L263-L288).
The local-export source uses provider `graphiti-entity-edges`, version `v0.31.2`, and the same
caller-declared project and exact group fingerprints. The file is an operator-produced projection,
not an official export or a live database read. It is bounded to 2 MiB, 5,000 edges and 10,000
source-episode references. Unknown versions, foreign groups, duplicate IDs and malformed fields fail.

`read_claims` returns a snapshot and private in-memory claims containing hashed edge, entity and
source-episode identities and source-declared validity times. The registry-facing `read` returns only
the snapshot, so concurrent callers do not share a mutable claims attribute. An endpoint or source episode need not be in the selected file; its hash
is not proof that it exists or that the relationship is true. Journal records fingerprint claims but
do not persist those reference fields as graph receipts. Content, names, embeddings and attributes
are not retained as text; row hashes still have equality and guessing risks. Empty or partial files
never prove complete capture. No actual Graphiti deployment or independently captured edge dump has
been tested, and the projection is not full provider support.

## Entity-node declarations

`GraphitiEntities` reads an operator-supplied JSON array shaped like pinned Graphiti
[`EntityNode` objects](https://github.com/getzep/graphiti/blob/d72407dcedac911e1be9fc15b55e8c05a3efcc36/graphiti_core/nodes.py#L499-L505).
The local-export source uses provider `graphiti-entity-nodes`, version `v0.31.2`, and the same
caller-declared project and exact group fingerprints. Input is capped at 2 MiB and 5,000 entities;
foreign groups, duplicate identities, malformed fields and timezone-free creation times are rejected.

Each selected node becomes a private contextual assertion. Its scoped identity fingerprint uses the
same domain as the endpoint claims described above, allowing a caller to compare explicit IDs in the
same selected group. Equality alone does not authenticate the project, prove a relationship, establish
independence or make the claim true. Node names, summaries, attributes and embeddings are not retained
as text; row hashes retain equality and guessing risks. Empty files do not prove complete capture. No
live provider or independently captured entity dump has been exercised.
