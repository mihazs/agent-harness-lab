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

## Execution identity and declared coverage

The optional immutable contracts in `sources.execution` accompany records without
adding fields to strict v1 `SourceSpec`, `EvidenceRecord` or `ImportReceipt` objects:

| Contract | Captured metadata |
| --- | --- |
| `RecordRef` | Source/native-record/revision fingerprints |
| `IdentityField` | Field name, namespace, optional fingerprint, status, provenance, evidence references, observation/effective times and missing/conflict reason |
| `ExecutionIdentity` | A subject record revision and at most one receipt per supported identity field |
| `CoverageManifest` | Expected/observed scope, adapter/parser versions, scan bounds, missing fields, gap reasons, truncation/limit flags and reported completeness |
| `ExecutionMetadata` | Up to 1,000 subject identities and an optional coverage manifest |

Identity fields cover project/worktree, original task/root, session/parent, attempt,
requested model alias, served model, runtime/version, effective harness/config,
memory snapshot, code/PR head and grader revision. Each value is a namespaced
fingerprint or explicit unknown; a requested alias never supplies a missing served
version. Unknown fields carry `null`, not the fingerprint of an empty string.
Conflicted fields carry distinct alternative fingerprints and no selected value.
References must resolve to revisions in the same explicitly scoped source. A late
import or changed current checkout cannot replace a historical field or its time.

These receipts are claims. The field schema can represent `verified` for a future
trusted execution verifier, but **every current export, registry and store import
rejects verified execution fields**. A native/controller provenance label, source
provider name or valid checksum cannot grant that capability. Existing Git and
instruction-inventory captures do not establish which configuration an execution
loaded. Native receipt mapping, verified joins and analyze/doctor integration
remain open in #42/#44/#46/#58. No receipt authorizes execution or grading.

Fields and collections use fixed bounds: 16 supported identity names, eight evidence
references or conflict alternatives per field, five scan dimensions and enumerated
missing/gap reasons. Timestamps require timezones, validity intervals must be ordered,
and unknown keys or mutable collections are rejected. Coverage's
`reported_completeness` is a source assertion; import completion and the public
summary's `capture_completeness: unknown` remain separate from that assertion.

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
pagination cycles. Native cursor tokens and snapshot bodies are not stored here:
adapters must manage those privately and pass their fingerprints.

Snapshot collectors use the explicit `snapshot_sha256` argument instead of
`cursor_after`. Snapshot content may legitimately return to an earlier state,
for example when an instruction edit is reverted. The journal retains the latest
snapshot fingerprint separately from a chained checkpoint fingerprint. A changed
snapshot gets a new checkpoint linked to its predecessor, even when that content
was observed earlier; an unchanged snapshot reuses its checkpoint. Native revision
deduplication still preserves the first attribution. This collector checkpoint is
not a new native revision and does not establish a historical execution identity.
Opaque pagination cursors keep the existing cycle and stale-writer checks.

Journal schema 3 retains snapshot fingerprints against their collector checkpoints
and distinguishes the currently observed snapshot from an opaque cursor. Returning
to opaque mode preserves earlier associations but clears the current snapshot
claim. Legacy cursor fingerprints are never retroactively relabeled as snapshots.
Schema-1 and schema-2 journals remain readable; status does not rewrite them. The next successful import
atomically upgrades the journal, preserving records, source attribution,
supersession claims and checkpoints. A snapshot import also records a new explicit
snapshot observation; legacy associations remain unknown.
Failed imports preserve the original bytes.
Older AHL versions cannot read a schema-3 journal; keep the previous journal when
rollback to an older client is required.

Schema 3 adds capture metadata envelopes keyed by source and committed snapshot
checkpoint. An envelope binds the source specification used at capture time, raw
source snapshot fingerprint and immutable execution metadata. Nonempty metadata is
included in the effective snapshot identity: a metadata-only change, parser upgrade
or recurring earlier receipt creates a new chained checkpoint without inventing a
new native record revision. Empty metadata preserves the prior snapshot identity.
No different envelope may overwrite a checkpoint. Migration creates no historical
identities for old imports, and records/metadata/cursors commit atomically.

Pass typed metadata to `ingest(..., snapshot_sha256=..., metadata=...)`; this first
metadata path requires snapshot mode. `capture(source, cursor)` returns immutable
metadata for a specific committed checkpoint without reading the live source.
`status` exposes a summary only for the active captured snapshot, keeping unknown
fields and declared coverage distinct. Historical envelopes survive a return to
opaque mode even though the current snapshot claim is cleared.

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
leaves the prior checkpoint intact. Restoring an older snapshot records a fresh
collector checkpoint without erasing history or inventing a native revision.
Status reads cache metadata without opening record data. Probe validates only the
declaration and filesystem path metadata. Disabled/unsupported versions do not
open record data. Registry collectors are registered explicitly by trusted Python
code; there is no dynamic plugin loading or automatic source enablement.

Provider-specific API transports, native formats, append offsets, analyze/doctor source
integration and remote pagination remain open in #42 and provider issues.

### Normalized export v2

The additional `local_file/ahl-metadata/v2` capability keeps the declaration and
record schemas at version 1. Its data payload contains exactly `schema_version: 2`,
`records` (the same bounded v1 record list) and `execution_metadata` (a complete
`ExecutionMetadata.to_dict()` object). The existing 8 MiB export bound applies to
the entire payload. Use each contract's strict `parse`/`to_dict` methods; no raw
native IDs, source bodies, private paths or credentials belong in these objects.
Versions outside v1/v2 are rejected before normalized payload reads. v1 list exports
remain supported and create no inferred execution metadata.

Preview includes the bounded execution summary, original `snapshot_sha256` and
`checkpoint_snapshot_sha256` that binds the source specification and nonempty
metadata. Status's `snapshot_sha256` is that committed effective snapshot identity;
`source_snapshot_sha256` retains the raw collector fingerprint. The registry and
direct store validate source ownership, available evidence references and metadata
claims before committing. No configured plugin or verification flag can promote
an export assertion to verified execution evidence.

## Offline Git and selected harness metadata

The `local_git/git/v1` provider uses the same explicit declaration, registry,
preview/import/status and private journal. Installing AHL enables no source.
Its declaration's `data` points to a separate JSON selection descriptor under
the authorized declaration root:

```json
{
  "schema_version": 1,
  "checkout": "project",
  "git_dir": "project/.git",
  "common_dir": "project/.git",
  "harness_paths": ["AGENTS.md", ".claude/settings.json"],
  "max_commits": 100
}
```

All three directories are explicit relative paths under that root. A linked
worktree names its own administrative Git directory and the shared common Git
directory; its marker, backlink and common-directory pointer must match those
selections. The source specification uses `identity_provenance: source_declared_id`,
`auth_mode: none` and `enabled: true`. Its root fingerprint is the declaration
directory fingerprint described above; its scope fingerprint is `digest(selection)`
using `sources.schema.digest`. Independently supply the expected project/scope
fingerprints. The reader does not infer project identity from path names, remotes,
branch names or timestamps. Keep the explicitly chosen journal outside the source
directories.

Preview invokes the trusted system `/usr/bin/git`, version-checked for Git 2.43+
in major version 2. Missing/unsupported tools block the read. Source configuration
is copied as bounded data and parsed with includes disabled solely to identify
supported repository/object/reference formats. Git operates against a private
temporary bare view containing copied regular object-store files and a sanitized
configuration. Source hooks, filters, signatures, aliases, fsmonitor settings,
credential helpers, global/system configuration and remote settings are not applied.
There is no checkout, source-index refresh, submodule update, fetch or lazy fetch.
Network protocols are disabled and the subprocess environment is cleared.

The view supports SHA-1 and SHA-256 loose objects and indexed packfiles. Full strict
`fsck` verifies the frozen object store before deriving history or diff statistics;
read-back commit, tree and selected blob identities are also checked. Ref names,
commit IDs, messages and touched paths become fingerprints. Per-worktree private
ref namespaces come from the selected administrative directory. Dangling refs are
excluded with a warning. History covers up to the declared 1–100 commits in HEAD
ancestry; merge differences use the first parent. Shallow boundaries retain known
parent identities while leaving unavailable differences unknown. Neither signed
metadata nor a commit date proves authorship quality, causal effect or independence.

`GitExport.harness` is a frozen `HarnessSnapshot` of only the selected paths. Each
file records its path/content fingerprints, current permission bits and its
committed content fingerprint/Git mode, including missing and untracked states.
The snapshot retains the captured HEAD identity when later captures see a different
HEAD. `configuration_source` identifies declared project files; observed loading
and effective settings remain `unknown`. File presence, names and contents do not
prove that an agent loaded them. Preview includes these metadata in `git_summary`;
import keeps normalized development-artifact records in the journal. Collector
checkpoints describe committed captures, not the current live source or a historical
execution's version. Full instruction/import/override graphs, hook lifecycle
inventory, independently evidenced execution bindings and hosting-provider
reconciliation remain open in #46/#58/#77–#80.

The first profile caps source reads at 512 MiB, filesystem entries at 10,000,
directory depth at 64, refs at 1,000 and selected paths at 100. Selection JSON is
bounded to 64 KiB and each current harness file to 1 MiB. Git output is limited to
4 MiB per command and 64 MiB per capture, with one ten-second admission deadline
and child CPU/address-space limits. Source filesystem operations are checked
between bounded reads but cannot be preempted at an exact deadline. Links,
nonregular files, alternate object stores, bare repositories, reftable repositories
and selected submodule/nested-repository contents are blocked without fallback.
All observed source metadata are rechecked before returning; failed reads leave
the committed journal untouched. Private copies are removed after success or error.

Synthetic native tests cover ordinary/linked/detached/shallow repositories, packed
and SHA-256 objects, corrupt blobs, private ref namespaces, dirty/untracked/missing
files, literal paths, mutable snapshot recurrence, inert execution settings,
missing-object remote denial, timeout cleanup and unchanged source contents/index.
These checks establish the tested read policy, not general hostile-binary isolation
or independently verified historical execution lineage.

### Declared Codex project instruction inventory

An optional `instruction_profile` in the Git selection enables bounded instruction
discovery within the selected checkout. For the first documented capability contract:

```json
{
  "schema_version": 1,
  "runtime": "codex",
  "version": "0.166.0",
  "task_cwd": "src/task",
  "fallback_filenames": ["TEAM.md"],
  "project_doc_max_bytes": 32768
}
```

The runtime/version, task location and configuration are caller declarations. The
reader does not execute Codex, inspect its global configuration or establish that
these settings were effective in any run. Other runtime/version combinations return
an unsupported inventory instead of applying Codex precedence. This contract follows
the [official instruction-discovery documentation](https://learn.chatgpt.com/docs/agent-configuration/agents-md),
checked 2026-11-01; synthetic resolution tests are not native loading evidence.

For the supported declaration, discovery examines each directory from the checkout
root through `task_cwd`, considering `AGENTS.override.md`, `AGENTS.md` and the explicit
fallback names in order. The first non-empty UTF-8 file is declared applicable;
other non-empty candidates in that directory remain visible as shadowed. Selected
files outside that directory chain are out of scope; unrecognized filenames within
it remain unsupported. Parent and shadowing edges describe declared hierarchy,
without resolving semantic contradictions in the text. Imports, path-scoped Claude
rules, managed/global influence and hooks are outside this first profile.

`GitExport.instructions` is a frozen `InstructionInventory`. Its fingerprint binds
the profile, task cwd, all inspected file hashes/modes, missing-file states and the
captured `HarnessSnapshot` fingerprint. Preview exposes only hashes and bounded
metadata; import adds a fingerprinted development-artifact inventory record through
the existing journal. No raw file text/path, applicability decision or discovery
result grants permission to edit a file. Observed loading and ambient influence
always remain unknown. A changed cwd, profile, HEAD, file content or mode invalidates
the receipt. Consumers must also retain the enclosing source/project identity.

The profile allows at most 32 cwd components, eight fallback basenames and a declared
project instruction cap of 0–1 MiB. Files use the existing 1 MiB per-file and shared
Git capture read/deadline bounds; non-UTF-8 files, links, nested repositories and
submodule crossings fail the capture. The collector does not guess native partial
file truncation: when selected non-empty files and separators exceed the declared
byte cap, resolution is blocked and affected applicability remains unknown. A
blocked or unsupported receipt is not a resolved effective instruction closure.

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
## Explicit GitHub REST captures

`sources.github.GitHubAPI` is an optional standard-library reader for one explicitly
authorized GitHub.com repository. Construct `GitHubSelection(repo_id, issues=(...),
pulls=(...))` with at most ten distinct ascending issue/PR numbers. Its
`project_sha256` binds the native repository ID and its `sha256` binds the selection.
Use `SourceSpec(kind="api", provider="github", version=SOURCE_VERSION, ...)` with
the endpoint fingerprint as `root_sha256`, those project/scope fingerprints,
`identity_provenance="source_declared_id"`, and explicit enablement. Register the
reader with `Capability("api", "github", SOURCE_VERSION,
("contextual_assertion", "development_artifact"))` to use the shared Registry and
atomic journal. This first remote API is programmatic; it adds no remote CLI
configuration or automatic network discovery.

The endpoint must be exactly `https://api.github.com/repos/OWNER/REPO`. Public reads
may use `auth_mode="none"`; configured/private reads require authorization explicitly
supplied to the constructor. The reader never loads GitHub CLI credentials, browser
cookies or environment tokens. Use a token with only the read permissions needed
for the selected resources. Repository ID, canonical resource URLs and visibility
are checked before child reads; private/internal visibility without authorization
blocks capture. Tokens and full repository names are absent from returned metadata.

The first capability pins REST API `2026-03-10` in the request and checks the selected
version response header. See [GitHub API versioning](https://docs.github.com/en/rest/about-the-rest-api/api-versions).
It captures issue/PR state, hashed titles/bodies/labels/milestones, timestamps,
base/head/merge references, proposed file changes, PR commit sequences/parents and
three distinct discussion forms: issue-style conversation comments, inline review
comments and review submissions. See GitHub's [conversation comments](https://docs.github.com/en/rest/issues/comments),
[inline comments](https://docs.github.com/en/rest/pulls/comments) and
[review submissions](https://docs.github.com/en/rest/pulls/reviews) contracts.
Native IDs and parents determine associations; matching body text never merges IDs.
Open-PR test-merge references remain distinct from merged results. Review decisions
retain their specific commit and whether it exactly matches the captured head;
a missing commit leaves that comparison unknown. Approval/merge/closure is a process
event or claim, never an executable grade or independent outcome.

Only fixed GET endpoints are used. Repository-supplied URLs are checked as native
references and never followed as commands or arbitrary fetch targets. Pagination
may advance only to the next numeric page of the same selected collection, with
100 items per page. Every initial read, page and identity recheck shares the
configured JsonAPI request, byte and deadline bounds (defaults: 20 requests,
8 MiB and 60 seconds, with per-request timeouts). PR file/commit/comment counts are
checked against listings to reject documented API caps or incomplete pagination;
GitHub documents maxima of [3,000 PR files and 250 PR commits](https://docs.github.com/en/rest/pulls/pulls).
Issue/PR identities and head/state metadata are rechecked before returning a
snapshot. HTTP errors, inaccessible resources, version mismatch, changing heads,
conflicting native parents and malformed/limited responses fail without returning
a snapshot or advancing a checkpoint. There is no automatic retry.

`GitHubAPI.summary` contains bounded hash-only nodes and native relationship
metadata from the last successful capture. That graph stays in memory; structured
relationship persistence and verified joins remain #58 work. The snapshot imports
fingerprinted typed records and declared partial coverage through journal v3.
Raw comment/code/message text is neither stored nor printed. Fingerprints remain
private metadata rather than an anonymization guarantee. The reader emits no
execution identities, inferred tombstones, causal labels or scientific independence.

This first slice leaves thread resolution/outdated state unknown without native
GraphQL evidence. Timeline events, CI/checks, per-commit file detail, commit comments,
conditional/incremental/deletion handling, time windows and remote CLI integration
remain open in #45/#42. An omitted comment is not proof of deletion or no defect.
Readbacks limit head inconsistency but do not make multiple REST calls an atomic
server snapshot. Native unittest fixtures cover the client contract using fake
HTTPS responses; live GitHub compatibility, concurrency and diagnostic utility
are not established by those fixtures. No source or server is contacted by install,
help, constructor invocation or default discovery.

No source is enabled by installation. Tests use synthetic metadata; they establish
contract validation, checkpoint behavior and a side-effect-free registry, not
real-source compatibility, containment, diagnostic usefulness or certification.
