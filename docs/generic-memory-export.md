# Generic offline memory exports

[Source contracts](source-contracts.md) · [Provider compatibility matrix](memory-compatibility-matrix.md)

`GenericMemoryExport` is a Python source connector for a caller-selected JSON array or JSONL file.
It does not discover files, query a provider, authenticate a source, run a model, or infer that an
imported memory is a correct task outcome. This generic mapping is not a provider compatibility claim.

The existing local-export manifest must declare an enabled `local_file` source with provider
`generic-memory`, version `v1`, `source_declared_id` provenance and no authentication. The manifest
binds the input file and its root as described in [source contracts](source-contracts.md). Construct
`GenericMemoryExport(manifest, format=..., project=..., namespace=..., fields=...)`, where `format`
is `json` or `jsonl`, and `fields` is an explicit mapping with the four keys `id`, `revision`,
`project` and `namespace`. Each value names a distinct top-level string field in every row.
The declared project's fingerprint must equal `digest(project)`, and the declared scope's must equal
`digest(namespace)`. Every row must carry those exact project and namespace values. The caller may
register the connector as a `contextual_assertion` capability with the existing source registry.

Input is capped at 2 MiB and 5,000 records. JSON must be an array; JSONL contains one object per
nonblank line. Duplicate object keys, missing identities, duplicate identities and mismatched scopes
are rejected. The file and declaration are rechecked after reading. Native identity is scoped by
namespace; a changed declared revision **or** changed row creates a new revision. The result contains
hashes rather than row bodies and reports partial coverage with no effective-context binding.

A row's hash covers the whole row, including unmapped fields. Hashes retain equality and can reveal
low-entropy guessed values; private source journals are not anonymized and must not be published.
The mapping and scope are declarations, not evidence that a provider authenticated them. This version
does not map graph edges, validity intervals, deletion or supersession, or report complete capture.
Provider-specific adapters, source-format compatibility and authorized live retrieval remain separate work.

## Inspect declared links

`generic_memory_links.links` accepts an already captured snapshot, the corresponding in-memory rows,
the same scope and identity mapping, and the name of a field containing declared links. It verifies
every row against the exact captured identity, revision and content fingerprint before inspecting links.
Each declared link is an object with `relation` (`cites`, `derived_from` or `supersedes`) and `target`
(the target's native ID in the same namespace). A missing or null links field is unknown; an empty list
is an observed empty declaration. The reader retains each target's scoped fingerprint even when the
target is missing, and reports presence only when its record is in the same snapshot. It makes no
network requests, establishes no truth or independence, and does not persist these private in-memory
claims as graph receipts. Target fingerprints retain the same guessing and equality risks as other hashes.
