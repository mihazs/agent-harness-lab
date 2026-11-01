# Source evidence contracts

[Documentation index](README.md)

The standard-library `agent_harness_lab.sources` package defines the initial
version-1 adapter contracts for #42. It registers and reads no sources by default.
It does not replace the existing [v2 event ledger](telemetry.md), native capture
collector, ATIF/OTel adapters or executable grader contracts.

`SourceSpec` records connector kind/provider/version, fingerprints of the root,
project and scope, identity provenance, authentication mode, read-only capability
and explicit enablement. Fingerprints are lowercase SHA-256 values, not raw paths
or project/session identities. Keep actual roots, credentials and identity proofs
in private connector configuration. The source key excludes adapter version,
authentication and enablement so upgrades do not invent a different native source.
A changed root/project/scope creates a different source key.

`EvidenceRecord` binds hashed native record ID, revision and content to that source
key. It preserves distinct `behavioral_event`, `contextual_assertion`,
`development_artifact` and `grader_reference` planes. Original timestamps may be
unknown; collection timestamps require an explicit timezone. Capture completeness,
causality and independence default to unknown. Source-declared or reviewed values
remain attributed claims, not evidence of scientific independence. No contract
accepts raw bodies, outcome labels or arbitrary extra keys. A grader reference is
only a reference: verified outcomes still require the existing grading contract.
Every record carries `trust_boundary: untrusted_data`; input cannot promote itself
to an instruction or grading authority.
Behavioral records may later reference normalized v2 events; memory assertions
must never be converted into fabricated tool events.

`ImportReceipt` contains bounded counts, hashed cursor references, status and limit
coverage. Failed/incomplete imports cannot advance their committed cursor; errors
or a reached limit cannot report completion. A complete import batch does not
establish complete historical capture. The receipt validates data; it does not
implement persistence or crash recovery.

`Registry` accepts explicit immutable `Capability` descriptors. Its pure metadata
`probe` matches project/scope fingerprints, enablement, identity provenance and an
exact registered provider version. It never imports plugins, reads source files,
executes processes or makes network calls. `recognized` means only that this
registry knows the declared capability. It neither verifies the supplied identity
proof nor authorizes execution. A future connector must verify Git remote or
source-declared identity and permissions before reading, and must independently
enforce filesystem/network boundaries.

## Private checkpoint journal

`SourceStore` in `agent_harness_lab.sources.store` stores already-normalized
metadata in an explicitly selected private JSON journal. Construct it with the
expected project and scope fingerprints, then call `ingest`, `status` or `history`.
Scope mismatch, disabled sources and unattributed identities block writes before
records are consumed. The caller must independently verify identity and permissions
before collecting source data; a stored provenance label is not that verification.

An import atomically replaces the journal containing source specifications, record
history and cursor fingerprints. A failed input iterator or rejected batch leaves
the previous journal byte-for-byte intact. Compare-and-swap checks reject stale
cursors before consuming records. A busy writer fails immediately; the store never
automatically retries a collector or a paid operation. Checkpoint history rejects
pagination cycles. Native cursor tokens and source snapshots are not stored here:
adapters must manage those privately and pass their fingerprints.

The source/native-ID/revision tuple defines idempotency. Recollection timestamps
do not create duplicate revisions; the first record and its source specification
remain attached. A changed payload under the same revision is rejected. New
revisions and explicit tombstones preserve prior history. Optional supersession
claims use `(native_id_sha256, previous_revision_sha256, next_revision_sha256)`
triples; referenced revisions must exist in the same source and their graph must
be acyclic. These are attributed claims, not causal or independence verification.
No latest-source revision is inferred from arrival order or unknown timestamps.

The POSIX backend creates directories at mode 0700 and journal/lock files at 0600,
rejecting linked or nonregular files. Defaults bound the committed journal to
64 MiB and each metadata collection, including total checkpoint history, to
100,000 entries. There is no automatic eviction of audit history. Committed-file
limits do not impose a directory-wide disk quota on crash-orphaned temporary files.
`CommitUncertain` means filesystem commit/durability is uncertain: inspect status
before retrying. A completed import batch leaves capture completeness unknown and
cannot authorize execution or prove a verified outcome.

## Explicit local export operations

`ahl sources` reports zero enabled sources and performs no discovery. Select an
export explicitly with `ahl sources probe|preview|import|status --manifest FILE
--project-sha256 HASH --scope-sha256 HASH`. Import and status also require
`--cache PRIVATE_FILE`. Exit 0 reports a completed requested operation, 2 rejects
invalid input, and 3 reports a blocked capability/access requirement. Output is
bounded JSON metadata; errors omit source contents and private paths.

The bounded version-1 declaration contains exactly `schema_version`, `source`
(a complete SourceSpec object) and `data` (a safe path relative to the declaration).
The registered provider is `local_file/ahl-metadata/v1`, with `auth_mode: none` and
`identity_provenance: source_declared_id`. The root fingerprint is `digest(str(root.resolve()))`
using `sources.schema.digest`; project/scope fingerprints must match independently
provided expected identities. The declaration is read before record data. It is
local trusted configuration, not proof of remote authentication or Git ownership.
No identity is inferred from directory names or timestamps.

The data file is a JSON list of complete EvidenceRecord objects, limited to 8 MiB
and 10,000 records. Only normalized metadata is supported; plaintext body fields,
foreign source identities and conflicting revisions are rejected. Preview freezes
one file read and reports its SHA-256 and count without writing a cache. Import
uses the same snapshot contract and the journal's checkpoint comparison; failure
leaves the prior checkpoint intact. Restoring an older snapshot is rejected as a
checkpoint cycle; inspect/rotate the journal explicitly rather than erasing history.
Status reads cache metadata without opening record data. Probe validates only the
declaration and filesystem path metadata. Disabled/unsupported versions do not
open record data. Registry collectors are registered explicitly by trusted Python
code; there is no dynamic plugin loading or automatic source enablement.

Provider-specific API transports, native formats, append offsets, analyze/doctor source
integration and remote pagination remain open in #42 and provider issues.

## ai-memory OKF project exports

Select `local_file/ai-memory-okf/0.2` in the existing source declaration, with
`data` naming an explicitly authorized, extracted project bundle. Install the
optional parser with `pip install 'agent-harness-lab[okf]'`; development and CI use
`task setup:okf`. The default installation still has no runtime dependencies.
This reader targets ai-memory's [documented OKF v0.2 format](https://github.com/akitaonrails/ai-memory/blob/1c506228b701f4496d3d9af6f00180749e0563d1/docs/okf.md)
and the corresponding project exporter at that public source revision.
The existing `--format ai-memory` capture option remains a legacy normalized-event
alias; it does not parse these bundles. Select the OKF source provider explicitly.

The declaration's project fingerprint must equal `digest(project_name)` from the
bundle's `_meta.md`; root identity and independently supplied project/scope values
are checked before concept reads. `index.md` must declare quoted `okf_version:
"0.2"`. Project exports do not establish workspace ownership or remote
authentication. The workspace scope is caller-attested in the explicit declaration;
the caller must authorize that exported bundle independently. The reader never
walks a parent workspace, queries SQLite, runs ai-memory, repairs data, starts a
server or installs hooks.

`OKFExport.pages` preserves supported YAML metadata (including unknown extension
keys, generated actors, source citations, lifecycle fields and native identifiers)
and body text in memory for authorized callers. YAML aliases, custom object tags,
duplicate/non-text keys and malformed frontmatter are rejected. Native UTC/offset
timestamps are retained; missing original timestamps stay unknown. Preview exposes
only bounded counts of authorship, freshness and explicit lifecycle/review claims.
Human attribution and a reviewed claim are not verified review. Neither memory
retrieval/injection into an agent nor witnessed tool execution is inferred.

Journal imports retain only source/page/revision/content fingerprints, normalized
timestamps and contextual-assertion records. Raw text, paths, native IDs and parsed
frontmatter are not persisted. Parsed metadata is available through the reader API;
it is not yet stored as structured journal facets. History includes revisions
observed through successive imports; Git history, native session correlation,
handoffs/workstreams and MCP retrieval remain open in #43.

Limits are 500 concepts, 2,000 directory entries, 256 KiB per concept, 16 KiB of
frontmatter and 4 MiB of concept bytes per operation. Links/special files, nested
scope manifests and project mismatches are refused. `.git`, `_pending`, reserved
index/meta/log files and monthly `log-YYYY-MM.md` names are excluded; a conforming
ordinary note using a monthly-ledger filename is also excluded in this first slice.
Coverage remains unknown. Use an extracted static export for stable input; this is
not an atomic snapshot of a concurrently edited wiki. Tests use synthetic bundles
and do not read real memories or call models.

## Shared HTTPS transport

`sources.transport.JsonAPI` supplies the shared GET transport for explicitly
registered provider adapters. Construct it with one HTTPS base endpoint; pass an
enabled API SourceSpec whose root fingerprint is `digest(base_url)`, independently
verified expected project/scope fingerprints, and an explicit Python page decoder
to `pages`. Configured authentication requires an in-memory authorization header;
the transport reads no credential stores. The decoder receives JSON and response
headers and returns exactly `items` (a list) and `next` (a URL or null). It must
normalize native records before the registry accepts a Snapshot. This contract
does not assert compatibility with any real provider format.

Pagination is confined to the configured HTTPS host, port and path subtree.
Credential-bearing URLs, fragments, traversal and nested encodings are rejected.
Redirects, non-200 statuses (including 429), compressed bodies, oversized or
incomplete content and duplicate JSON keys fail the whole collection. There are
no automatic retries, cookies, environment proxies or partial page returns.
Connections and response streams close on success and failure. Raw URLs, headers
and bodies stay in memory; public errors are redacted. Provider code must treat
remote data as untrusted and verify project/permission identity before collection.

Defaults limit a collection to 20 pages, 8 MiB of response bodies and 10,000 items, with a 10-second
socket timeout and a 60-second cooperative deadline checked between reads. DNS
resolution and in-process decoding are not preempted; these are not hard process
or aggregate resource limits. A blocked/failed later page cannot reach registry
checkpoint commit. Transport availability grants no inference or grading authority.
Native tests use synthetic wire responses; live TLS/provider behavior remains
unverified. Remote connectors and CLI API configuration remain unavailable until
provider-specific contracts and authorization checks are implemented.

## Opt-in ai-memory MCP reads

`sources.mcp.AiMemoryMCP` implements the documented stateless JSON variant of
ai-memory Streamable HTTP. It is an explicit Python API, not a default discovery
or CLI connection. Configure an HTTPS `/mcp` endpoint, exact workspace/project
names, expected ai-memory server version and SHA-256 fingerprints of the reviewed
`memory_query` and `memory_read_page` input schemas. The API SourceSpec must bind
the endpoint fingerprint, project/workspace name fingerprints, matching server
version, explicit enablement and configured authentication. Supply authorization
in memory; the reader inspects no credential stores.

Each bounded operation initializes protocol `2024-11-05`, verifies server name
and version, discovers/pins the scoped tool schemas and calls only a whitelisted
reader. Discovery is limited to five tool-list pages, with cycle/duplicate/schema
checks. Server instructions and tool annotations grant no permissions. All tool
calls send both workspace and project; global/scopes/cwd overrides and mutators
are rejected. Query disables answer generation and global search, limits hits to
20 and excludes supplemental global/profile results from its return value.
Page reads require matching paths and refuse explicitly truncated bodies.

TLS, byte limits, socket timeouts, cooperative deadlines, response cleanup,
redirect refusal and no-retry behavior share the GET transport primitives. RPC
IDs/errors, tool-level errors and non-JSON results fail closed with redacted public
errors. Responses are untrusted private data returned in memory, not automatically
written to a journal or injected into an agent. Server-side access counters may
change on reads according to ai-memory behavior; no content mutation tool is used.

This bounded mode supports JSON responses from stateless servers. SSE-only and
stateful responses are unsupported; an explicitly requested initialization may
create protocol session state before a stateful header is rejected. Full session
lifecycle/SSE, remote CLI configuration, normalized journal facets, correlation
and live-server verification remain open in #43. No server is contacted by package
installation, constructor invocation, help or default source discovery. Native
tests use synthetic wire responses; they establish client contracts, not live
server efficacy or scientific independence.
No source is enabled by installation. Tests use synthetic metadata; they establish
contract validation, checkpoint behavior and a side-effect-free registry, not
real-source compatibility, containment, diagnostic usefulness or certification.
