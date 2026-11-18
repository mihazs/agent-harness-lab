# Offline LangGraph item projection

[Compatibility matrix](memory-compatibility-matrix.md) · [Source contracts](source-contracts.md)

`LangGraphItems` reads a caller-supplied JSON array of `Item.dict()` objects in the shape defined by
[LangGraph's pinned store class](https://github.com/langchain-ai/langgraph/blob/08a2016953789a4f579b4d36bd1967941ec62012/libs/checkpoint/langgraph/store/base/__init__.py#L51-L112).
The pin identifies LangGraph `1.2.22`; the `Item` implementation at that commit belongs to
`langgraph-checkpoint` `4.3.2`. Each row must contain exactly `namespace`, `key`, `value`, `created_at` and `updated_at`. This is an
operator-produced file, not an official provider export. The reader never opens a store, performs a
search, refreshes a TTL, starts a model, uses credentials or contacts a service.

Use an explicitly authorized local-export manifest whose enabled source is `local_file`, provider
`langgraph-store-items`, version `v1.2.22`, source-declared identity and no authentication. Construct
`LangGraphItems(manifest, project=..., namespace=...)` with one nonempty namespace tuple of at most
32 bounded strings. The source project fingerprint must equal `digest(('langgraph.project', project))`
and its scope fingerprint must equal `digest(('langgraph.namespace', namespace))`. Every row must
match the entire namespace, not merely its prefix. The selected project is a caller declaration: a
LangGraph item has no project or account field that authenticates it.

The reader caps the file at 2 MiB and 5,000 items, rejects duplicate keys, unknown fields, foreign
namespaces, invalid timestamps and updates older than creation, and rechecks the file and manifest.
Timestamps must include a timezone. Each item becomes a private contextual assertion. A fingerprint
of the complete row supplies a derived revision; the provider did not report a revision chain. Values
are not retained as text, but hashes expose equality and may reveal guessed low-entropy values. Private
journals are not public. Empty and short dumps do not prove complete capture or absence of memories.

This is an experimental offline projection of the pinned data shape. A synthetic row emitted by the pinned `langgraph-checkpoint` `4.3.2` serializer passed the
optional compatibility check. No live provider or independently captured deployment dump was exercised. It does not claim full LangGraph or LangMem support,
verified outcomes, deletion tracking, or effective-context binding.
