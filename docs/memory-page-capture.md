# Explicit ai-memory MCP page captures

The optional Python API `sources.memory_capture.MCPPageCapture` connects the
existing `AiMemoryMCP` read-only client to Registry and SourceStore. It retrieves
an explicit selection of page paths from one configured project without an
application-imposed count limit.
It does not search for projects, inspect an active-project pointer, discover
credentials, run ai-memory, query SQLite, install hooks, or write memory content.
Constructor, registry probe and journal status make no network request.

```python
from agent_harness_lab.sources.memory_capture import MCPPageCapture
from agent_harness_lab.sources.registry import Capability, Registry

# client is the existing pinned AiMemoryMCP; source binds its endpoint,
# workspace/project fingerprints, exact server version and configured auth.
collector = MCPPageCapture(client, ('notes/example.md',),
                           authorization=explicit_authorization)
registry = Registry()
registry.register(Capability('api', 'ai-memory-mcp', source.version,
                            ('contextual_assertion',)), collector)
snapshot = registry.preview(source, project_sha256=source.project_sha256,
                            scope_sha256=source.scope_sha256)
# registry.ingest(source, store, **scope) uses the same capture and atomic journal.
```

The existing static-scope protocol is documented by upstream
[programmatic-memory.md](https://github.com/akitaonrails/ai-memory/blob/72e4f4273a5cfc6cbfd3384766021b7b26b282b3/docs/programmatic-memory.md).
The pinned public
[ReadPageArgs and response implementation](https://github.com/akitaonrails/ai-memory/blob/72e4f4273a5cfc6cbfd3384766021b7b26b282b3/crates/ai-memory-mcp/src/server.rs)
supports `max_chars` up to 64,000. Each read still checks the explicitly configured
server version and exact reviewed tool-schema fingerprints; this is not automatic
support for all future server versions. Only stateless JSON responses are supported.

Capture reads each selected page and then reads it back in reverse order. Missing
body/path/completeness, truncation, changed responses, tool errors, source changes
or exhausted bounds refuse the capture before a checkpoint can commit. An isolated
per-operation reader keeps the request scope fixed even if the caller changes its
client configuration mid-capture. Every read sends both workspace and project and
only invokes `memory_read_page`; no server instructions or tool annotations grant
permission. The supplied authorization remains in memory and errors are redacted.
Server-side access counters may still change when pages are read.

Optional caller-selected deadlines, response-byte budgets, tool-discovery page
limits and RPC request limits cover initialization, all selected reads and
readbacks, and normalization. None is imposed by default. There is no application
page-body ceiling. Reads request the maximum advertised by the pinned schema,
falling back to the documented upstream 64,000-character maximum. The pinned
upstream page API has no offset argument: a longer truncated page is unavailable
for complete capture, rather than silently imported as a full page. An explicit
per-read `max_chars` bound is also honored by `AiMemoryMCP.read_page`.
Each stateless page read initializes and pins discovery again under the same
optional budgets. The coverage `pages` bound counts all wire RPC replies, including discovery;
selected records and page/readback counts are reported separately.
DNS and in-process work are not hard-preempted. Readbacks detect observed
changes; the server supplies no transactionally atomic multi-page snapshot.

The private journal stores only source/page/revision/content hashes, collection
time, `contextual_assertion / retrieved_memory_page` records, and partial coverage
metadata. Raw bodies, paths, page IDs, session IDs, frontmatter and authorization
are not persisted. Requested scope is explicitly configured; returned metadata
is an untrusted source claim, not authenticated ownership or an execution receipt.
The capture identity binds selection, parser, configuration and complete returned
page metadata, so changed native revision claims remain historical observations.
Coverage names every execution identity field missing, and never claims complete
project or lifecycle capture. Reads are **retrieved context**, never proof of
installed, injected, consumed or effective context, native execution, truth,
permission, task success, independent roots or causal benefit. Human/reviewed
frontmatter claims do not gain review authority.

This is the selected-MCP-page journal slice of issue #43. Remote CLI configuration,
SSE/stateful sessions, native session correlation, handoff/workstream interpretation,
as-of validity, deletion reconciliation and actual observed injection remain open.
The legacy telemetry `--format ai-memory` is still only a normalized-event alias.

Run `task test:sources:memory` for synthetic wire controls. The global installed
suite's existing `test_source_*.py` selector includes the new capture module;
its tests assert the installed package root. No real memories, credentials,
optional ML dependency or paid inference are needed.
