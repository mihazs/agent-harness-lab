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

The reader bounds input to 2 MiB, nesting depth 64 and 5,000 episodes, rejects duplicate identities and malformed or
foreign rows, and rechecks the input and manifest after reading. Each episode becomes a private
contextual assertion. Its revision fingerprints the selected response row; Graphiti did not supply a
native revision. The row hash can expose equality or low-entropy guesses, and journals are not public.
An empty response and a short result do not establish complete capture; upstream does not expose a
complete snapshot here. Episode content, names and descriptions are not stored as text. Missing graph
nodes, edges, episode links, source changes and provider lifecycle details remain unobserved.

The projection is experimental: its fixture mirrors the pinned public response construction, but no
real deployment or independently captured provider response has been checked. It does not imply full
Graphiti support or verified task outcomes.
