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

`ImportReceipt` contains nonnegative integer counts, hashed cursor references, status and limit
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
| `ExecutionMetadata` | Subject identities and an optional coverage manifest, without a fixed count ceiling |

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
loaded. Preview/status and the explicit private cache context in analyze/doctor
already expose these declarations and missing fields. Verified execution-receipt
production and verified cross-source joins remain separate work in
#44/#45/#46/#53/#58. The optional [live controller source](controller-sources.md)
path observes actual pinned workers and fresh graders; its immediate verification
expires with the live capability, and its journal still stores declarations.
No receipt authorizes execution or grading.

Identity names, scan dimensions and missing/gap reasons remain enumerated and
unique. Subject identities, evidence references and conflict alternatives have no
fixed count ceiling. A scan bound uses `null` for an unlimited scan, or a
nonnegative integer for an explicitly selected limit; observed counts must remain
nonnegative and cannot exceed a finite limit. Timestamps require timezones,
validity intervals must be ordered,
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

Normalized local v1–v3 exports require matching readbacks of their selected
payload and declaration before returning a checkpoint-ready snapshot. The reader
rechecks project/scope, enablement and the original data-file selection before
each payload read. Payload bytes and record counts have no fixed ceiling. An
observed edit, removal or symlink
blocks the operation and leaves an existing committed checkpoint unchanged. A
changed selector is rejected before reading its replacement, even if its bytes
would match. Declaration formatting and payload mtime alone do not change the
snapshot identity. This detects changes seen at the read boundaries; it is not
an atomic filesystem snapshot or proof that no transient change occurred. Stable
exports retain their previous raw-byte SHA-256, v1–v3 metadata and cursor behavior.

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

Journal schema 4 retains snapshot fingerprints against their collector checkpoints
and distinguishes the currently observed snapshot from an opaque cursor. Returning
to opaque mode preserves earlier associations but clears the current snapshot
claim. Legacy cursor fingerprints are never retroactively relabeled as snapshots.
Schema-1, schema-2 and schema-3 journals remain readable; status does not rewrite them. The next successful import
atomically upgrades the journal, preserving records, source attribution,
supersession claims and checkpoints. A snapshot import also records a new explicit
snapshot observation; legacy associations remain unknown.
Failed imports preserve the original bytes.
Older AHL versions cannot read a schema-4 journal; keep the previous journal when
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

Schema 4 adds a nullable evidence graph alongside nullable execution metadata in
the same capture envelope. A nonempty envelope must contain at least one of them.
Old captures gain no graph or invented links; their effective snapshot/checkpoint
hashes stay unchanged. A graph contributes its canonical fingerprint to the capture
identity, so relationship-only edits and recurring prior graphs preserve distinct
checkpoints without duplicating native record revisions. Records, both metadata
facets and the cursor still commit in one atomic replacement.

The source/native-ID/revision tuple defines idempotency. Recollection timestamps
do not create duplicate revisions; the first record and its source specification
remain attached. A changed payload under the same revision is rejected. New
revisions and explicit tombstones preserve prior history. Optional supersession
claims use `(native_id_sha256, previous_revision_sha256, next_revision_sha256)`
triples; referenced revisions must exist in the same source and their graph must
be acyclic. These are attributed claims, not causal or independence verification.
No latest-source revision is inferred from arrival order or unknown timestamps.

The POSIX backend creates directories at mode 0700 and journal/lock files at 0600,
rejecting linked or nonregular files. The committed journal and its metadata
collections have no default size ceiling. Callers can provide positive byte/record
limits, including for total checkpoint history. There is no automatic eviction of audit history. Committed-file
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

The data file is a JSON list of complete EvidenceRecord objects, without an
implicit byte or record-count limit. Only normalized metadata is supported; plaintext body fields,
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
Versions outside v1/v2/v3 are rejected before normalized payload reads. v1 list exports
remain supported and create no inferred execution metadata.

Preview includes the bounded execution summary, original `snapshot_sha256` and
`checkpoint_snapshot_sha256` that binds the source specification and nonempty
metadata. Status's `snapshot_sha256` is that committed effective snapshot identity;
`source_snapshot_sha256` retains the raw collector fingerprint. The registry and
direct store validate source ownership, available evidence references and metadata
claims before committing. No configured plugin or verification flag can promote
an export assertion to verified execution evidence.

### Normalized export v3 and relationship claims

`local_file/ahl-metadata/v3` adds a payload with exactly `schema_version: 3`,
`records`, `execution_metadata` and `evidence_graph`. The record/declaration schemas
stay at version 1. Execution metadata and the graph may independently be null;
otherwise use their strict `to_dict()` representations. Payload bytes and collection counts have no implicit size ceiling. Old v1/v2 payloads retain their existing shapes and behavior.

`sources.graph.EvidenceGraph` contains the source, project and scope fingerprints,
adapter version, exact `RecordRef` nodes and `JoinReceipt` claims, without
fixed collection-size ceilings. A join names its relation, namespace, resolver version, both endpoints
(with an explicit null for an unavailable target), evidence references,
status, basis, reason and nullable validity interval. All non-null endpoints and
evidence must be graph members with existing revisions in the authorized source.
Foreign sources/projects, dangling revisions, duplicate entries, unknown relation
types and invalid intervals reject the capture. Canonical node/join ordering keeps
reordered graphs idempotent. No raw IDs, source text, paths or credentials belong
in these contracts.

This persistence layer retains candidate, conflicted and unlinked claims. Native
field and review labels cannot self-certify: claimed `verified`/`reviewed` joins are
rejected through normalized export, Registry, direct ingestion and journal reads.
Persisted native proofs, reviewed/cross-source resolution, grouping/deduplication,
contradiction diagnosis and diagnostic-utility trials remain open in #58. A stored
link is an investigation lead, not proof of a common cause or an independent task.
`independent_roots` remains null and verification/execution authority stays false.

Preview/status expose only bounded graph scope/version/hash/status summaries.
`SourceStore.graph(source, cursor)` reads the immutable graph at a committed
checkpoint without rereading the source; omitted cursors select the current
checkpoint. Graph changes do not rewrite old endpoints, including after late
imports or revisions. `task test:sources:graph` runs the native contract,
migration, atomicity and GitHub mapping cases.

### Offline context in analysis and readiness reports

`ahl analyze PROJECT` and `ahl doctor PROJECT` may explicitly inspect a private
source journal with all three arguments:

```sh
ahl analyze PROJECT --source-cache PRIVATE_JOURNAL \
  --source-project-sha256 EXPECTED_PROJECT_HASH --source-scope-sha256 EXPECTED_SCOPE_HASH
ahl doctor PROJECT --json --source-cache PRIVATE_JOURNAL \
  --source-project-sha256 EXPECTED_PROJECT_HASH --source-scope-sha256 EXPECTED_SCOPE_HASH
```

No journal is searched for or opened without these options. The reader validates
the existing journal against the independently supplied source project/scope and
uses one read-only snapshot. It never invokes a connector, reads credentials,
contacts a service, runs a model or upgrades the on-disk journal. Unsupported safe
filesystem capabilities, malformed caches, foreign scope and linked files reject
the requested context. A missing/disappearing journal is explicitly `missing`,
with unknown capture completeness; it is not a successful empty corpus.

The complete inventory reports source fingerprints/kinds, fingerprinted provider
and adapter/parser versions, cached revision/checkpoint counts, declared execution
identity fields, coverage gaps/bounds/limits and graph status counts. Provider,
version and namespace labels from untrusted metadata are not echoed. Every source
and adapter-version fingerprint is shown. Fingerprints remain private metadata, not an
anonymization guarantee. A declared complete scan remains a source assertion.

The report's target-project binding is `unverified`: selecting a scoped source
journal does not establish that its project identity equals the analyzed directory.
Independent roots and historical capture completeness remain unknown, and no
graded outcome or execution authority is granted. Exact cross-source/target binding
still requires the #58 evidence resolver; directory names and matching text cannot
provide it.

Analyze stores context beside its telemetry analysis in private `analysis.json`
and renders a separate section in `report.md`. The context does not add events,
findings or evaluation failures. Doctor adds an optional context check and bounded
JSON/text summaries; it cannot make profile prerequisites pass. Default behavior
without the options stays unchanged. `improve`/`watch` do not implicitly consume
this context. `SourceStore.inventory()` and `sources.context.load(...)` provide
the same offline metadata view, and `task test:sources:context` runs its native
scope, privacy, lifecycle and reporting tests.

## Native Codex capture journals

The explicit `local_file/codex-rollout/0.166.0-v1` provider connects the pinned
Codex reader to the shared registry and private source journal. It uses the existing
local declaration format, with `data` naming a JSON selection descriptor:

```json
{
  "schema_version": 1,
  "project": "project",
  "capture": "captures/rollout-example.jsonl",
  "max_records": 10000
}
```

Both paths are explicit, normalized relative paths under the declaration root.
The project directory must exist, and the capture's declared session/turn cwd must
match it. The root fingerprint binds the declaration directory; the scope
fingerprint is `digest(selection)`. Independently supply expected project/scope
fingerprints. Directory names do not establish repository identity. Use
`identity_provenance: source_declared_id`, `auth_mode: none` and explicit enablement.
Probe and cached status do not read native captures, execute runtimes or connect
to accounts. Preview/import reuse the same bounded reader as explicit telemetry.

The adapter preserves exact native-record fingerprints and approved type/timestamp
metadata as behavioral evidence. Bodies, arguments, results, native IDs, paths and
credentials are not copied to the journal. A body-only edit versions its native
record and source snapshot without changing the ordinary metadata-only monitor
fingerprint. Collection time remains separate from reported native time.
Derived unmatched-call coverage has a stable call-based identity and unknown
provenance; resolving another pending call cannot relabel its history.

Session-start and turn-context subjects receive versioned execution metadata.
Project/cwd, thread, producer/runtime version, explicit parent-thread and requested
model references remain **declared**, with exact evidence-record revisions. A
fork parent is not substituted for a parent-thread identity. Requested model is
attached only to its declaring context; delayed events do not inherit later
settings. Served model, actual loaded harness/configuration, memory injection,
task root, grader outcome and independence remain unknown. Producer fields and
labels cannot authenticate a runtime or upgrade themselves to verified evidence.

Records, declared identity/coverage metadata and exact per-checkpoint membership
nodes commit atomically through the existing schema-4 journal. Append, edit and
late import preserve old record revisions,
model declarations and checkpoint membership; identical re-imports are idempotent.
The v1 profile re-reads one bounded file; it does not claim a full-history byte
cursor. The explicitly versioned root profile below shares multi-file discovery. Archive sources with different declared scopes
remain separate observations until independently resolved.

The same graph includes `candidate` native-file relationships: each actual native
record belongs to its session header (`native_session_member`), and an unambiguous
linked tool result references its one native call (`tool_result_of`). These use
resolver `codex-native-links-v1` and exact endpoint revisions. Duplicate calls or
results, missing IDs and out-of-order results do not get a tool-pair link. Derived
unmatched-call coverage gets no native membership link. These associations describe
what the selected file records; they do not authenticate the producer or establish
an independent task root.

Explicit `sources recheck` supports only the built-in pinned Codex reader. It
re-reads and validates the current selection, requires a complete final line and
compares exact current relationships through the shared resolver. A fresh native
match can be reported `verified` with resolver `codex-native-recheck-v1`; edits
invalidate old endpoint revisions, invented links stay candidates, and conflicts
remain unresolved. Claimed historical intervals are cleared. The report changes
neither the private journal nor its candidate receipts. Verified here means current
native-file relationship only: execution identities remain declared, task outcomes
and independent roots remain unknown. Imported labels, file-export substitutes and
reader subclasses cannot supply verification authority. No network/model calls are
used. A partial, malformed, unsupported or mutated native file produces no report.

An uncommitted final line can be previewed as a complete prefix with
`checkpoint_ready: false`. Registry import rejects that snapshot before creating
or changing a cache/checkpoint. `Snapshot.checkpoint_ready` is an explicit boolean,
defaulting to true for existing providers; it does not turn ordinary historical
coverage gaps into import failures. Failed/malformed reads, foreign scope,
unsupported versions, traversal, links, resource expiry and changed declarations
likewise cannot advance the committed cursor. Selection and source input are
revalidated after conversion and Snapshot validation.

Native source reads stream JSONL records without implicit file, line, record,
subject-count or time ceilings. The optional `max_records` selection is enforced
when supplied; omission or `null` means unlimited. Coverage `records` counts
observed input lines; a `null` scan limit declares no caller-selected bound.
Coverage remains partial even at native stop.
The parser mapping is `codex-native-journal-v1`; this is not execution certification.
`task test:sources:codex` runs native scope, privacy, history, relationship recheck,
admission and failure controls; installed wheel/archive source tests include them. Incremental
byte-position imports and verified cross-source execution joins remain
open in #42/#44/#58. Explicit analysis still owns v2 event-ledger projection.

### Explicit bounded root journal

The separate `local_file/codex-rollout/0.166.0-v2` profile selects a bounded native
root with the same declaration/expected project/scope checks. Its private selection
JSON uses exactly these fields:

```json
{
  "schema_version": 1,
  "project": "project",
  "source_root": "captures",
  "session": null,
  "max_files": 100,
  "max_scan_files": 2000,
  "max_records": 10000
}
```

Use `session: null` for the declared project's accepted threads, or an exact native
thread ID in this private descriptor. Both paths remain normalized relative paths
under the declaration directory; `scope_sha256` is `digest(selection)`. No home or
account discovery runs. The existing guarded root scanner selects only uncompressed
`rollout-*.jsonl` files, including active/archive child directories, and checks
bounded headers before full reads or the accepted-file allowance. Foreign projects
and other selected threads remain excluded. Public summaries contain fingerprints
and bounded counts, not paths, native IDs or bodies.

Merge identical copies and overlapping prefixes before correlation. Each native
thread keeps its own header membership and call/result namespace; matching text and
shared declared root labels do not establish independent origins. Moving a capture
within the selected root preserves native record history and old checkpoint graphs.
A body-only edit versions its native record. Incompatible raw revisions at the same
owned native ordinal across selected copies reject the entire journal batch; the
adapter never chooses one file's body as authoritative. Even a locally suppressed
usage record retains its ordinal claim, so a duplicate response cannot hide another
file's incompatible event. Ordinary telemetry still compares approved metadata and
counts each response once; journal admission additionally checks raw fingerprints.

The entire root scan, correlation, record conversion, identity/graph validation,
selection revalidation and final selected-file/directory verification share one
stable-input lifecycle. There are no implicit byte, line, filesystem-entry,
directory-depth, time or collection-count ceilings. Optional descriptor fields
`max_files`, `max_scan_files` and `max_records` preserve caller-selected limits;
omission or `null` means unlimited. Coverage declares the selected record/file
limits and actual counts; `native_summary.bytes_read` reports cumulative reads
without declaring an aggregate byte bound. The root mapping version is
`codex-native-root-journal-v1`.

Preview exposes `checkpoint_ready: false` for any selected partial tail, reached
file/scan cutoff, unsupported header, skipped unsafe entry or empty accepted scan.
Those previews can neither import nor recheck relationships. Hard resource errors,
conflicting claims or concurrent selected-input/descriptor changes reject the
operation. No incomplete batch can create or advance a checkpoint, and successful
imports retain partial historical coverage. Private cached status reads no capture
files. Root-profile recheck uses the same exact built-in reader and current-only
relationship contract as v1. Incremental byte-position capture, compressed/base
history, Claude native version coverage and cross-source execution proofs remain
open; no model calls or event-ledger emission occur during source operations.

## Native Claude capture journals

The explicit `local_file/claude-jsonl/2.1.316-v1` provider connects the pinned
[Claude file profile](telemetry.md#explicit-claude-code-21316-file-profile) to the
same private journal and registry. Its local declaration uses a selection file
with exactly `schema_version`, `project`, `capture` and `max_records`, as in the
Codex single-file example. Both paths are normalized relative paths under the
declaration directory. Supply independently expected project/scope fingerprints;
`scope_sha256` is `digest(selection)`. Use explicit enablement, source-declared
identity and local filesystem authorization. No home/account/sidecar discovery or
Claude execution occurs. Probe and cached status read no capture files.

The source accepts one explicitly attributed native writer. Actual block/message
observations receive exact canonical native-row revision fingerprints; omitted
body edits add history without changing ordinary telemetry's metadata fingerprint.
Two copies of the same native UUID with different raw revisions reject the batch,
even when their approved metadata matches. Ordinary telemetry retains its existing
body-insensitive duplicate behavior. Computed response facets and unmatched-call
coverage remain derived records with unknown provenance, never additional native
execution receipts. Stable call-based pending identities cannot be reassigned when
another call resolves. Files, line sizes, input/normalized counts and the entire
conversion/validation/revalidation lifecycle remain bounded.

Each native message has at most one execution-identity subject, even when it
contains multiple normalized blocks. Session ID, project/cwd, producer and version
are declared claims. A native assistant's model report may appear as a **declared**
`served_model` identifier in namespace `claude.reported_model`; it is not a verified
served version, and cannot populate requested-model aliases. Source timestamps and
the exact supporting record revision remain attached. Actual effective model,
loaded harness/configuration, memory injection, task root, attempt, parent session
and grader bindings remain unknown unless independently substantiated. Every
current importer rejects verified execution fields. A current checkout change or
late model report cannot rewrite old metadata or graph membership.

The graph keeps native message membership and parent-UUID pointers separate from
session lineage. Multi-block members reference their containing native message's
representative record (`native_message_member`); one representative carries its
native parent pointer (`native_message_parent`). Missing parents remain `unlinked`.
Self/cyclic pointers remain literal data with no traversal or lineage inference;
a reflexive pointer references its one evidence record once.
Unambiguous native tool results can reference their one native call through the
same bounded exact-ID logic as Codex. Derived facets get no native relationship
edges. Message pointers, repeated text, shared session labels and source counts
never establish independent roots or a task outcome.

Explicit `sources recheck` uses only the exact built-in Claude reader and a fresh
complete scoped capture. It compares current native relationships, preserves stale
revisions and explicit conflicts, and clears caller-supplied historical intervals.
It does not modify the journal or promote stored candidates. Native file-field
consistency does not authenticate the producer, actual execution, lineage or model
serving. File exports, reader subclasses and imported verified labels cannot
provide that authority. A partial tail, failed/malformed read, hard limit, unsafe
path or changed selection/input cannot create or advance a checkpoint or emit a
recheck proof. Successful imports still have partial historical coverage.

The mapping version is `claude-native-journal-v1`, with up to 10,000 input/source
records and 1,000 identity subjects. `task test:sources:claude` runs native unittest
controls; wheel/archive source checks include them. The source shares selection,
file guards, revision mapping, identity wrapping and graph assembly with Codex.
`task test:sources:native:compat BASELINE=FROZEN_CHECKOUT` compares both Codex preview
contracts, including metadata/graph/checkpoint hashes, against an explicitly
selected baseline. Ordinary test runs skip this optional frozen-baseline check.
Native hooks/configuration are never executed. Sidecar relationships, incremental
byte cursors, live wire validation and cross-source execution joins remain open in
#42/#44/#58; bounded project-root selection is the separate profile below.

### Explicit bounded Claude project-root journal

The separate `local_file/claude-jsonl/2.1.316-v2` profile mirrors the Codex root
profile for Claude's per-project session directories. Its private selection JSON
uses exactly these fields:

```json
{
  "schema_version": 1,
  "project": "project",
  "source_roots": ["claude/projects", "archive/projects"],
  "session": null,
  "max_files": 100,
  "max_scan_files": 2000,
  "max_records": 10000
}
```

Each `source_roots` entry is a non-overlapping, existing, normalized relative
directory under the declaration directory that has the layout of a Claude
`projects` directory; there is no home, `CLAUDE_CONFIG_DIR` or account discovery.
`session` is `null` or one exact native session UUID; `scope_sha256` is
`digest(selection)` and the mapping version is `claude-native-root-journal-v1`.

**Established from the pinned wire reference.** Directory naming follows the
[SDK 0.2.176 session reader](https://github.com/anthropics/claude-agent-sdk-python/blob/2146f9d8985513053e904b13200c8f744b36785a/src/claude_agent_sdk/_internal/sessions.py)
(`_sanitize_path`, `_find_project_dir`, `_resolve_subagents_dir`): every
character outside `[a-zA-Z0-9]` of the NFC project path becomes `-`; names over
200 characters are truncated and suffixed with `-` plus a 32-bit base-36 hash.
Because the CLI and SDK hashes can differ, truncated names also select sibling
directories that share the 200-character prefix, as the reader does. A session is
`<project-dir>/<session-uuid>.jsonl`; subagent transcripts are
`<project-dir>/<session-uuid>/subagents/**/agent-*.jsonl`. Anthropic does not
document this layout as a stable public contract: it is the pinned reader's
observed schema, and synthetic fixtures (not a live CLI) validate the mapping.
Project paths containing characters outside the Basic Multilingual Plane are
rejected because JavaScript sanitizes UTF-16 code units and that mapping is not
established here.

**Scope.** Only the mapped directories inside the selected roots are listed and
read. For a project name of at most 200 sanitized characters, no other project's
directory is opened. For a truncated name, the root's directory names are listed
and every sibling sharing the 200-character prefix is selected, listed and
header-read like the exact directory; a different project's files there are then
rejected by record scope, not by name. Without a session, a mapped directory is
listed once without recursion: top-level `<uuid>.jsonl` files are candidates, and
only `<uuid>/subagents` directories are descended. With a selected session only
its main file and its `subagents` directory are inspected. Each `subagents` tree
is bounded to 64 directory levels below `subagents` and 10,000 visited entries of
any kind; reaching either bound stops that tree, records an `unknown` receipt with
reason `subagent_tree_limit` and reached limit `subagent_tree`, and blocks the
checkpoint. Links, hard-linked and special files are skipped without being
followed and counted as unsafe entries.

Names narrow the scan and must agree with the records; they never establish
identity alone. Each candidate is read only up to its first attributable
envelope, whose explicit `version`, `cwd` and `sessionId` decide admission.
Sanitization is lossy, so a colliding project's file in the same directory is
rejected as `foreign_project`. A top-level `<uuid>.jsonl` must carry that
`sessionId` with `isSidechain: false`; a `<uuid>/subagents/**/agent-<id>.jsonl`
must carry parent `sessionId` `<uuid>`, `isSidechain: true` and `agentId` `<id>`,
the filename mapping the pinned reader documents. Any disagreement yields an
`unknown` receipt with reason `native_location_mismatch` and blocks the checkpoint.

**Merge rules.** Claude publishes no archive directory. An archived or backup copy
is declared as another selected root with the same layout. Accepted files are
grouped by native session and writer (`isSidechain`, `agentId`, `teamName`) and
replayed through one v1 projection per group, so identical copies and overlapping
prefixes deduplicate by native UUID, response usage/stop facets are computed once
over the merged group, and incompatible approved metadata or raw revisions for one
UUID reject the entire batch. Moving a file between roots or directories preserves
native record identity and history. Main and subagent writers remain distinct
cases; sidechain owners, parent sessions, task roots and independence stay
unknown exactly as in the file profile, and parent pointers are never traversed.

**Coverage receipts.** `native_summary.receipts` lists one fingerprinted receipt
per examined `.jsonl` file with status `accepted`, `rejected` or `unknown` and a
reason: `scoped_native_records`, `partial_native_tail`, `foreign_project`,
`native_location_mismatch`, `native_scope_unavailable` (no attributable envelope,
such as metadata-only files), `unsupported_native_version`,
`unsupported_native_envelope`, `invalid_native_record`, `partial_native_scope`,
`unrecognized_native_layout`, `subagent_tree_limit` or `not_read_after_file_limit`. `native_summary.files` carries each accepted file's
SHA-256, input records, partial tail, duplicates and unsupported counts;
`discovery` counts selected and missing project directories, scanned, header-read,
accepted, rejected and unknown files, unsafe entries, filesystem entries, bytes and
reached limits. Paths, native IDs and bodies stay out of summaries.

**Admission.** Optional `max_files`, `max_scan_files` and `max_records` bound the
run as in the Codex root profile; omission or `null` means unlimited, and coverage
declares the selected record/file limits and counts without an aggregate byte
bound. Top-level directory listings have no implicit entry ceiling; `max_scan_files`
counts recognized candidates only. Preview reports `checkpoint_ready: false` for a
partial tail, reached file/scan/subagent-tree limit, unattributable, unsupported or
mislocated candidate, skipped unsafe entry or empty accepted scan; such previews cannot import or recheck. Unrecognized-layout
files are reported as `unknown` but not read, and do not block admission. Unknown
receipts never become accepted captures or failure labels. Hard resource errors,
conflicting claims and changes to selected files, scanned directories or the
descriptor reject the operation. Recheck uses the same exact built-in reader.

**Not established.** Git worktree aggregation, `CLAUDE_CONFIG_DIR`/home
resolution, session files outside the documented layout (not descended), `history.jsonl`, sidecar
or subagent parent resolution, compressed or relocated histories, incremental byte
cursors, `ahl analyze --source-root` for Claude, live CLI wire validation and
cross-source execution joins remain open in #42/#44/#58.

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
execution's version. Declared Codex and Claude Code instruction inventories are
described below; observed loading, managed/global and path-scoped rule graphs,
hook lifecycle inventory, independently evidenced execution bindings and
hosting-provider reconciliation remain open in #46/#58/#77–#80.

Git analysis does not impose default source-byte, entry, directory-depth, ref,
selected-path, selection-file or command-output size ceilings. Callers can select
explicit limits. Read-only Git invocation, path-safety and source-readback checks
remain in place; explicitly bounded callers retain their child resource guards.
Source filesystem operations are checked between reads but cannot be preempted
at an exact optional deadline. Links,
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

### Local Git graph and coverage profile

Select `local_git/git/v2` explicitly to project the same frozen Git view into the
shared execution-metadata and evidence-graph contracts. The selection descriptor
stays at schema version 1; no additional source reads, commands or network access
are enabled. The `v1` record set, raw snapshot fingerprint and absence of these
facets stay unchanged.

The graph binds exact captured commit parents, ref targets, a new immutable
`harness_snapshot` record, its selected file records and any declared instruction
inventory. `harness_based_on` identifies the snapshot's HEAD base; it does not
assert that dirty/untracked file content equals that commit. `harness_member`
records selected file metadata in that snapshot, including missing-file states.
Instruction inventory membership does not establish runtime loading.

Parent/ref targets outside captured commit history become content-free
`git_object_reference` records. Their native reference was observed, but their
full object/history was not captured as a commit record. Annotated tags retain
their direct tag-object target; the graph does not invent a peeled-commit edge.
All relationships remain native-field candidates requiring independent resolution.
They grant no execution authority, cross-provider identity or independent roots.
Admission rejects more than 10,000 nodes or 20,000 edges before allocating graph
rows/receipts, including oversized parent lists in shallow commits.

Coverage records captured commit/record counts and frozen-view bytes read under
the existing limits. Reaching the selected history ceiling marks truncation;
shallow history marks truncation even when the ceiling was not reached. Coverage
always remains partial because selected paths/history cannot certify a complete
execution capture. Execution identities are empty, and missing effective bindings
remain explicit. Source scope is caller-declared, not verified repository identity.

Preview exposes both facets; import commits records, graph, coverage and checkpoint
atomically through the existing journal. Cached status and explicit analyze/doctor
context can inspect those facets without reading Git. Older checkpoints retain
their captured graph when HEAD, refs or current files change. `task test:sources:git`
runs the native synthetic Git contract and compatibility cases. Historical execution
binding, cross-provider reconciliation and independent proof persistence remain
open in #46/#58.

### Explicit local Git relationship recheck

After importing a v2 Git capture, `ahl sources recheck` accepts the same explicit
manifest, cache and expected project/scope fingerprints. It reads the captured
graph at a fixed checkpoint, then performs a fresh capture through the built-in
frozen-view Git reader. It does not change the source, configuration, journal or
lock file. No network, model, credentials, repository hooks or checkout operation
is authorized by the recheck.

The resolver compares relation type and both exact source/native-ID/revision
endpoints. Current captured commit, ref and harness/instruction-membership
relationships can produce `verified` native-match receipts. This verifies the
captured relationship only: harness membership does not certify instruction
applicability, observed loading or runtime-effective settings. Reference-only
objects remain candidates, even when their hashes appear in native parent/ref
fields; missing object detail is not invented. Stale revisions and absent
identities remain unlinked, explicit conflicts remain conflicted, and unrelated
roots with identical file content do not establish ancestry.

Only the exact built-in Git reader and explicit v2 profile can perform this
recheck. Imported graph labels, custom reader classes, similar content and claimed
historical validity intervals cannot provide proof. Unvalidated labels are replaced
with fixed resolver metadata; original claims remain addressable by fingerprint.
The report uses `git-native-recheck-v1`, reports `network_used: false` and retains
unknown independent roots and unverified task outcomes. Its receipts have no
verified historical interval and cannot be imported as a self-certified graph.
The stored candidates and older checkpoints remain unchanged.

GitHub and Git use one relation-comparison implementation after their separate
built-in capture admissions. `task test:sources:recheck` runs native synthetic
checks for both. These fixture checks establish current relationship validation;
cross-source joins, persistent proof lifecycle and a reviewed diagnostic-utility
study remain open in #58.

### Offline Git/GitHub commit join

`sources.commit_join.join_commit(git_source, git_store, github_source, github_store,
repository_id=ID, commit_sha=SHA)` relates one commit across two already imported
private journals: a `local_git/git` journal and an `api/github` journal. It reads
only the selected journals: their latest checkpoints, plus their stored record
history to classify staleness. It reads no source, runs no Git,
makes no network or model calls and does not change either journal. There is no
CLI exposure yet.

The join key is the GitHub repository fingerprint `digest(('github.repository', ID))`
plus the exact full lowercase 40-hex SHA. Abbreviated, uppercase, mixed-case,
padded, 64-hex and non-string SHAs are rejected before any journal is read; no
case folding or prefix search is performed. SHA-256 object-format repositories are
outside this join. Both source specifications must declare that repository
fingerprint as their project. The GitHub journal's current checkpoint must contain
a current `github_repository` record. The built-in reader emits it only after
checking the numeric repository ID, but the join does not verify which reader
produced a stored journal, so the report labels this `journal_declared_current`. The Git checkout's repository membership remains a caller
declaration: local Git capture ignores remotes and source configuration, so no
local field verifies which hosted repository a checkout belongs to.

Membership comes from the latest active checkpoint's evidence graph nodes, so both
journals need graph-capable captures (`local_git/git/v2` and the GitHub `graph`
detail). The report's `status` is one of:

| Status | Meaning |
| --- | --- |
| `matched` | The exact commit record is a current node in both latest checkpoints under one repository fingerprint |
| `stale` | A commit record exists in a journal's history but not in its latest checkpoint (for example after a force-push or a rewritten PR), or the current revision is a tombstone |
| `unlinked` | The two sources do not declare the same repository fingerprint (`repository_identity_not_shared`), so an identical SHA in a fork, an unrelated repository or an undeclared checkout is not joined; this does not establish that the repositories differ |
| `unknown` | No repository ID was supplied, the GitHub repository record is absent, a latest checkpoint or graph is unavailable, the commit is unobserved, observed only as a Git reference, absent from a truncated history or ambiguous |

A `matched` report carries one `same_commit` `JoinReceipt` from the Git commit
record to the GitHub commit record, with the GitHub repository record as extra
evidence. It stays `candidate` with basis `source_declaration`, because the Git
side of the repository binding is declared and cached checkpoints are not a live
native recheck. Other statuses carry no receipt. `unknown` and `stale` are
coverage states. They are not failures, and `matched` is not a success: no task
outcome, grade, independent root, causal claim, verification or execution
authority follows. Applicability is `selected_cached_checkpoints_only` with native
freshness and historical intervals unverified. The report contains fingerprints
only, carries `publication_allowed: false` and profile `cross-journal-commit-join-v1`,
and is rejected by public benchmark export.

This join establishes exact commit-identity co-observation between two scoped
journals. It does not establish that the commit ran, was the PR head or merge
result when an outcome was produced, or that squash, rebase or cherry-pick commits
are related. Neither does it persist joins, add them to lineage components,
verify Git repository membership or inspect organization/user scope beyond the two
selected sources. `task test:sources:commit-join` runs synthetic negative controls
for foreign repositories, force-push staleness, abbreviated/case-variant SHAs,
missing repository identity and truncated history. It also runs one native case
through both built-in readers. The remaining cross-source chain, persisted proofs,
contradiction reports and the diagnostic-utility study stay open in #58.

### Declared Codex project instruction inventory

An optional `instruction_profile` in the Git selection enables instruction
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

`project_doc_max_bytes` is an optional caller-selected bound. Omit it or use `null`
to inventory instructions without an imposed content limit; a nonnegative integer
retains that exact bound without an additional application ceiling. Instruction
files are always read and hashed in full, including when the declared bound blocks
applicability. The instruction connector does not cap fallback names, selected
files, inventory nodes or hierarchy edges. Enclosing Git-source resource limits
remain separate from this inventory contract.

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
rules, managed/global influence and hooks are outside this Codex profile; the
Claude Code profile below declares bounded project imports only.

`GitExport.instructions` is a frozen `InstructionInventory`. Its fingerprint binds
the profile, task cwd, all inspected file hashes/modes, missing-file states and the
captured `HarnessSnapshot` fingerprint. Preview exposes only hashes and
metadata; import adds a fingerprinted development-artifact inventory record through
the existing journal. No raw file text/path, applicability decision or discovery
result grants permission to edit a file. Observed loading and ambient influence
always remain unknown. A changed cwd, profile, HEAD, file content or mode invalidates
the receipt. Consumers must also retain the enclosing source/project identity.

The profile imposes no cwd-depth, fallback-name or instruction-byte ceiling of its
own; only the optional declared `project_doc_max_bytes` and the enclosing Git
capture's read/deadline bounds apply. Non-UTF-8 files, links, nested repositories and
submodule crossings fail the capture. The collector does not guess native partial
file truncation: when selected non-empty files and separators exceed the declared
byte cap, resolution is blocked and affected applicability remains unknown. A
blocked or unsupported receipt is not a resolved effective instruction closure.

### Declared Claude Code project memory inventory

The same optional `instruction_profile` accepts one Claude Code declaration. It has
no fallback names or byte-limit field; a Codex-shaped profile for this version, or
this shape for the Codex version, is rejected:

```json
{"schema_version": 1, "runtime": "claude-code", "version": "2.1.317", "task_cwd": "src/task"}
```

The profile follows the public [Claude Code memory documentation](https://code.claude.com/docs/en/memory),
checked 2026-11-04. That page is not versioned per release, so the mapping from it
to `2.1.317` is an assumption. Other versions return an unsupported receipt.
The reader does not run Claude Code, read user/managed settings, or follow
anything outside the selected checkout. Synthetic tests check this
documentation-derived resolver; they are not native loading evidence.

What the receipt declares, never verifies:

- **Launch files.** In each directory from the checkout root through `task_cwd`,
  a non-empty UTF-8 `CLAUDE.md`, `.claude/CLAUDE.md` and `CLAUDE.local.md` are
  `declared_applicable` with reason `directory_concatenation`. They are
  concatenated, not shadowed. A whitespace-only launch file is hashed but stays
  `unknown` with reason `empty`. `parent` edges link consecutive directory
  levels; order within one directory is not encoded. Directories above the
  checkout are not read; their influence stays in `ambient_state: unknown`.
- **Imports.** An `@path` token must start a line or follow whitespace.
  Backslash-escaped spaces are kept and quoted tokens are skipped. A relative
  path resolves from the importing file's directory. Each newly declared file is
  expanded once, breadth-first, up to the documented maximum of four hops (a
  launch file is hop 0). A file reached at hop 5 is hashed but stays `unknown`
  (`import_depth_limit`) and is not expanded. Each distinct importer→target
  pair is one edge. After the graph is built, every edge inside a strongly
  connected import component (including a self-import) is `import_cycle`;
  others are `imports`. Cycle detection is linear in distinct edges.
- **Markdown constructs.** Tokens are skipped inside fenced code blocks (three or
  more backticks or tildes, also after `>` blockquote markers or a list marker;
  a backtick line with a later backtick is inline code, not a fence) and inside
  backtick code spans that close on the same line. A token is
  recorded but stays `unknown` with reason `import_markdown_context_uncertain`
  when it is in a code span left open across lines (until it closes or a blank
  line), or on a line indented by four or more spaces or a tab. Tokens inside
  `<!-- -->` comments stay `unknown` (`import_inside_html_comment`). Other
  Markdown structure, such as HTML blocks, link syntax and reference
  definitions, is not interpreted.
- **Unresolved references.** Missing targets, absolute, `~` and root-escaping
  imports, `.git` paths, directories and invalid paths are never read. Each
  importing file gets at most one content-free node per reason:
  `import_missing` (`missing`), `external_import_not_captured`
  (`external_reference`, `unknown` presence), `unsafe_import_target`,
  `import_target_not_regular_file` and `invalid_import_path` (`unsupported`).
  Their identity is the importer plus the reason; token counts and target names
  are not retained.
- **Readable but unknown targets.** In-checkout targets outside `task_cwd` are
  hashed but stay `unknown` (`external_import_requires_approval`), because
  Claude Code asks for approval first. Non-UTF-8 targets and files over the
  documented 4 MiB skip threshold also stay `unknown`.
- **AGENTS.md.** When a launch `CLAUDE.md` (including an empty one) is present,
  `AGENTS.md` and `.claude/AGENTS.md` in the chain are `shadowed` with reason
  `assumed_default_instruction_files_setting`. That reason means the documented
  default setting was assumed, not read: the setting lives only in user or
  managed configuration. Without a launch `CLAUDE.md` they are `unknown`. An
  `AGENTS.md` imported by a declared file becomes `declared_applicable` through
  that import.
- **Explicitly unknown.** Selected `.claude/rules/` files (`rules_not_modeled`)
  and launch filenames below `task_cwd` (`on_demand_subdirectory`) stay unknown.
  Files below `task_cwd` are not discovered unless the caller selects them.
  Managed and user files, `claudeMdExcludes`, approval decisions, on-demand
  truncation and path-scoped rule matching are not captured. A selected
  `AGENTS.override.md` or other unrecognized file in the chain is `unsupported`.

An import whose path crosses a symbolic link, a non-directory component or a
nested repository fails the capture, as does a linked final target or a non-UTF-8
launch file. Only a genuinely absent path is `import_missing`. Like the Codex
profile, files are read and hashed in full with no byte cap. Node count is bounded
by the launch candidates, the distinct in-checkout files reached within four hops,
five unresolved-reference nodes per expanded file and the selected files. The
receipt uses the existing `InstructionInventory` shape and `instruction_inventory`
record. Observed loading stays `unknown`, and the receipt grants no edit authority.
Content, import, cwd and profile changes invalidate it. Native
`InstructionsLoaded` evidence, managed and global capture, rules and on-demand
modeling, and execution binding remain open in #46.

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
only counts of authorship, freshness and explicit lifecycle/review claims.
Human attribution and a reviewed claim are not verified review. Neither memory
retrieval/injection into an agent nor witnessed tool execution is inferred.

Journal imports retain only source/page/revision/content fingerprints, normalized
timestamps and contextual-assertion records. Raw text, paths, native IDs and parsed
frontmatter are not persisted. Parsed metadata is available through the reader API;
it is not yet stored as structured journal facets. History includes revisions
observed through successive imports; Git history, native session correlation and
handoffs/workstreams remain open in #43. A separate explicit
[selected MCP page capture](memory-page-capture.md) now supports hash-only journals.

The reader imposes no concept, directory-entry, frontmatter, per-page or aggregate
byte ceilings. Links/special files, nested
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

Each operation initializes protocol `2024-11-05`, verifies server name
and version, discovers/pins the scoped tool schemas and calls only a whitelisted
reader. Discovery follows tool-list pagination to exhaustion, with cycle/duplicate/schema
checks. Caller-selected `max_pages`, `max_requests`, `max_bytes`, `timeout` and
`seconds` are optional; omitted limits impose no application ceiling. Server
instructions and tool annotations grant no permissions. All tool
calls send both workspace and project; global/scopes/cwd overrides and mutators
are rejected. Query disables answer generation and global search, forwards an
explicit result limit without an application maximum, and excludes supplemental
global/profile results. Without a supplied result limit, the server's query
default applies; a query is not a complete project inventory. Page reads request
the maximum advertised by the pinned schema, or the documented upstream 64,000
characters when the schema does not expose that maximum. The pinned read-page
contract provides no offset pagination. Truncated bodies, missing completeness
and inconsistent returned character counts remain unavailable for complete capture.

TLS, explicit byte limits, optional socket timeouts and cooperative deadlines, response cleanup,
redirect refusal and no-retry behavior share the GET transport primitives. RPC
IDs/errors, tool-level errors and non-JSON results fail closed with redacted public
errors. Responses are untrusted private data returned in memory, not automatically
written to a journal or injected into an agent. Server-side access counters may
change on reads according to ai-memory behavior; no content mutation tool is used.

This mode supports JSON responses from stateless servers. SSE-only and
stateful responses are unsupported; an explicitly requested initialization may
create protocol session state before a stateful header is rejected. Full session
lifecycle/SSE, remote CLI configuration, native execution facets, correlation
and live-server verification remain open in #43. The optional
[MCPPageCapture](memory-page-capture.md) adds selected-page Registry/SourceStore
captures with caller-selected shared bounds and readbacks. No server is contacted by package
installation, constructor invocation, help or default source discovery. Native
tests use synthetic wire responses; they establish client contracts, not live
server efficacy or scientific independence.
## Explicit GitHub REST captures

`sources.github.GitHubAPI` is an optional standard-library reader for one explicitly
authorized GitHub.com repository. Construct `GitHubSelection(repo_id, issues=(...),
pulls=(...))` with distinct ascending issue/PR numbers; the selection itself sets
no count limit. Its
`project_sha256` binds the native repository ID and its `sha256` binds the selection.
Use `SourceSpec(kind="api", provider="github", version=SOURCE_VERSION, ...)` with
the endpoint fingerprint as `root_sha256`, those project/scope fingerprints,
`identity_provenance="source_declared_id"`, and explicit enablement. Register the
reader with `Capability("api", "github", SOURCE_VERSION,
("contextual_assertion", "development_artifact"))` to use the shared Registry and
atomic journal. The explicit CLI configuration below provides the same reader;
installation and probes perform no automatic network discovery.

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

The REST profiles use only fixed GET endpoints. Repository-supplied URLs are checked as native
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
metadata from the last successful capture. That graph stays in memory by default;
the explicit v5 profile below persists revision-bound claims. Verified joins remain
#58 work. The snapshot imports
fingerprinted typed records and declared partial coverage through journal v4.
Raw comment/code/message text is neither stored nor printed. Fingerprints remain
private metadata rather than an anonymization guarantee. The reader emits no
execution identities, causal labels or scientific independence. Only the explicit
v6 window profile below records absence tombstones; earlier profiles infer none.

Version 1 leaves thread resolution/outdated state unknown without native GraphQL
evidence and does not read timelines or CI/checks; the explicit profile below adds
those reads. Later profiles add commit detail and bounded comment windows.
Conditional (ETag) and incremental requests remain open in #45/#42. An omitted
comment is not proof of deletion or no defect.
Readbacks limit head inconsistency but do not make multiple REST calls an atomic
server snapshot. Native unittest fixtures cover the client contract using fake
HTTPS responses; live GitHub compatibility, concurrency and diagnostic utility
are not established by those fixtures. No source or server is contacted by install,
help, constructor invocation or default discovery.

### Selected timeline and CI detail profile

`GitHubSelection(..., schema_version=2, details=("checks", "timeline"))` explicitly
selects the additional surfaces. Details are unique, sorted capability names;
select either or both. Use `selection.source_version` (`2026-03-10-v2`) in the
SourceSpec and registry capability. The scope fingerprint includes the flags.
The request API date remains `2026-03-10`. Version-1 selection serialization,
fingerprints, provider version and request set remain unchanged; new reads are
never enabled by upgrading the package alone.

The timeline profile reads each selected issue/PR's
[native timeline](https://docs.github.com/en/rest/issues/timeline). Supported stable
event IDs retain parent, event type, actor/time, optional commit reference and a
content fingerprint. Comment projections reuse their native comment record;
review projections link only when normalized native parent and revision metadata
match the captured review. Different projections do not imply independent roots.

GitHub's [cross-reference event](https://docs.github.com/en/rest/using-the-rest-api/issue-event-types#cross-referenced)
provides an explicit source issue but no stable event ID. The collector represents
that native issue pair while leaving distinct occurrence identity unknown. A
reference can link to an already selected issue only when repository URL, issue
number and native ID agree. Foreign references remain fingerprinted and unlinked;
the reader does not fetch their URLs. Unsupported or unidentified event forms,
including forms without a supported native identity, remain hash-only
`unmapped_timeline` diagnostics with partial coverage. They are not fabricated
native events or inferred absence of a relationship.

The checks profile reads [check runs](https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference)
and [commit statuses](https://docs.github.com/en/rest/commits/statuses#list-commit-statuses-for-a-reference)
for the captured full PR head SHA. Check-run IDs, suites/apps, status/conclusion
and timestamps remain separate from commit-status IDs, contexts and descriptions.
Names/descriptions/targets are fingerprints; external result URLs are never
followed. Every check/status binds to its requested native head and selected
repository. A successful CI state remains development-artifact process evidence,
not a grader reference or verified task outcome.

Check-run envelope totals must stay consistent across pages and match the listed
records; malformed counts, truncation, foreign URLs and wrong heads reject the
capture. GitHub's default latest-check filter and reference endpoint limits do not
establish complete check history, so `latest_checks_only` remains explicit. All new
calls share the original request/byte/deadline budgets, and final issue/PR/repository
readbacks still gate the snapshot. HTTP denial and an unavailable detail surface
cannot be reported as an empty successful check/review set. The explicit profile
below adds GraphQL thread state. Later profiles add commit detail, durable claims and
comment windows; conditional and incremental requests remain open.

### Authenticated native review threads

`GitHubSelection(..., schema_version=3, details=("threads",))` enables native
review-thread state. Version 3 also allows `checks` and `timeline`; flags remain
unique and sorted, and enter the scope fingerprint. Use `selection.source_version`
in the source and registry. Versions 1 and 2 retain their original serialization,
scope and request sets. Thread selection requires `auth_mode="configured"` and an
authorization value passed directly to the constructor, even for a public repository.
Missing authorization rejects capture before connecting. No token is discovered
from the environment or another application's credential store.

GitHub exposes [review threads](https://docs.github.com/en/graphql/reference/pulls#pullrequestreviewthread)
through GraphQL. The reader sends two fixed **query** documents to
`https://api.github.com/graphql`, using POST as prescribed by GitHub's
[GraphQL transport](https://docs.github.com/en/graphql/guides/forming-calls-with-graphql).
It accepts no user-supplied query or mutation. One query reads selected-PR threads;
the other continues a thread's comment connection. The shared fragment requests
only the fields needed for native identity, revision matching and thread state.
The source version identifies this query contract; the REST API date does not
version GitHub's GraphQL schema. Missing schema fields or denied access block
capture rather than implying an empty review set.

REST node IDs bind the GraphQL repository and PR to the already checked repository
ID and selected PR. PR head OIDs must agree. Each thread and comment must name the
same native parents. A comment joins only when its node ID, 64-bit database ID,
review ID, body/path, timestamps and current/original commits match the captured
REST fields. Full database IDs may be canonical decimal strings or integers;
missing IDs cannot be replaced by text similarity or array positions. Comment
membership must match the REST inline listing. Capture-wide bindings reject an
opaque GraphQL node ID reused for another native identity, PR parent or node type,
including across separate thread connections and selected PRs. Missing/deleted/inaccessible
members, contradictory projections and conflicting parents reject the capture.

Threads retain native resolved/outdated flags, file-path fingerprints, diff sides,
nullable line ranges, the captured PR head and comment membership fingerprints.
Inline comments obtain their thread's resolved state and their own native outdated
flag and line ranges. Thread and comment outdated flags remain separate. Null line
positions stay unknown. Resolution, approval and outdated state remain contextual
evidence, never a task grade or evidence of an independent outcome.

Both nested connections use 100-item cursor pages with stable totals, unique node
IDs, bounded cursors and exact final counts. All REST reads, GraphQL pages and
readbacks share the same request, byte and deadline limits. Repeated cursors,
partial HTTP-200 responses containing GraphQL errors, HTTP failures and truncation
fail without automatic retry or checkpoint advancement. A second thread capture
must match the first before a snapshot is returned. This detects observed changes;
it does not make a series of server reads atomic. Coverage remains partial.

Only fingerprints and approved metadata enter the snapshot. Query variables,
credentials, native IDs and raw comment text are not persisted. Structured joins
remain in memory unless the v5 graph profile is selected. Persisted/cross-source
resolution remains #58 work. Synthetic tests cover nested
pagination, exact cross-API identity, revision/state changes and atomic import
failure; they do not establish live GraphQL compatibility or diagnostic utility.

### Explicit immutable commit captures

`GitHubSelection(..., schema_version=4, commits=(FULL_SHA, ...))` adds an explicit
selection of unique, sorted full commit SHAs with no count limit of its own. Branch names, tags and
abbreviated SHAs are rejected. Commit selection enters the scope fingerprint;
versions 1–3 retain their original serialization and requests. Select commits
without issues/PRs, or alongside them. Public reads may remain unauthenticated;
the existing explicit configured authorization applies to private repositories.

The reader uses GitHub's [commit detail](https://docs.github.com/en/rest/commits/commits#get-a-commit)
and [commit comment](https://docs.github.com/en/rest/commits/comments#list-commit-comments)
GET endpoints. It retains the exact SHA, ordered parent fingerprints, separate
author/committer timestamps, tree fingerprint, reported statistics and comment
count. File records retain path/blob/previous-path fingerprints and change counts;
an absent blob SHA stays unknown. Raw patches, code, messages and comment bodies
are not returned or stored. Commit comments remain a fourth discussion form,
distinct from issue-style comments, inline PR comments and review submissions.

Each file page must repeat the same static commit metadata. File identities must
be unique, file statistics must agree with the aggregate, and comment listings
must match the native count and requested commit. Foreign URLs, contradictory
native IDs, metadata changes, malformed pages and the documented 3,000-file cap
reject capture. A second full read must match the first before checkpoint commit;
all pages/readbacks share the original request, byte and deadline limits. These
reads remain partial observations, not atomic snapshots or complete history.

An explicitly selected commit reuses its existing PR commit record when native
metadata agrees. Relations to captured parents and selected PR head/base/merge
references require exact SHAs; open-PR test-merge references remain separate from
reported merged results. No revert/cherry-pick relationship is inferred from a
message, and no comment or merge establishes a task outcome or independent root.
Structured relationships remain in memory unless the v5 graph profile is selected.
Persisted/cross-source resolution remains #58 work. Conditional and incremental
requests remain open in #45/#42; comment windows are described below.

`task test:sources:github:commit:live` runs the native public-service acceptance
case only with explicit `GITHUB_ENDPOINT`, `GITHUB_REPO_ID` and `GITHUB_COMMIT`
inputs. It uses no credentials and persists no response fixtures. Ordinary test
discovery skips all live cases. Synthetic cases establish client behavior; they
do not establish general provider compatibility or diagnostic effectiveness.

### Opt-in durable GitHub relationship claims

`GitHubSelection(..., schema_version=5, details=("graph",))` maps the existing
native relationship inventory to exact source-record revisions and imports it
through the shared graph contract. The flag changes authorized scope, uses no
extra requests and composes with the existing checks/thread/timeline flags and
explicit commit selection. Versions 1–4 retain their serialization and request
sets and return no graph unless the new capability is selected.

All emitted joins are candidates with a declared native-field basis; the collector
does not claim independent relationship resolution. Imported edges preserve PR
head/merge distinctions and specific review/comment revisions as investigation
leads. Historical graphs remain attached to their captures, and source state
changes cannot relabel an older graph. Structured relationships persist only with
this explicit profile; ordinary summaries remain in memory. Current native links
may be explicitly rechecked below; persisted/cross-source proofs and independent
root/grouping claims still require further #58 resolution and validation.

### Explicit comment windows and absence tombstones

`GitHubSelection(..., schema_version=6, since=SINCE, until=UNTIL)` selects a
half-open creation-time window `[since, until)` for issue-style conversation
comments and inline review comments. Both bounds are required canonical
RFC 3339 UTC seconds (`2026-01-01T00:00:00Z`). A bound must equal its own
re-serialization, so hour 24, leap seconds, offsets and fractions are rejected on
every supported interpreter. Reversed or empty windows and spans over 366 days are
also rejected. Version 6 accepts every v5
detail flag and explicit commit selection. Both bounds enter the selection
fingerprint, so different windows yield different scopes, source keys and
journals. Versions 1–5 retain their exact serialization, fingerprints and
requests and ignore committed history.

The window does not narrow requests. Each selected comment listing is still read
in full and must match its native count, so the observation basis stays the
complete listing. Every comment is validated; only in-window comments become
records. Timeline comment projections follow the same rule. Issues, PRs, reviews,
threads, files, commits, commit comments and checks are not windowed. A missing
creation time leaves membership unknown and blocks the capture.

On `import`, the registry passes the selected journal's committed revisions only
to collectors that explicitly opt in. A previously present in-window comment whose
native ID is absent from every complete listing of a fully successful capture
becomes a `tombstone` revision for that ID. The tombstone records
`deleted_or_unobservable` with `deletion_confirmed: false` and unknown
provenance. These REST listings do not report deletion, so the reader never
labels absence as deletion or distinguishes it from lost access.

Listings use numbered pages with a final count check, not a server snapshot. A
deletion and a creation between page reads can keep the count unchanged. A comment
that still exists can then shift onto an already-read page and be tombstoned. The
final issue/PR readbacks catch only changes in their compared fields. This
residual risk is one reason the label stays `deleted_or_unobservable`.

Each tombstone binds the present revision it supersedes. Repeated absence after
the same present revision reproduces the same tombstone and adds nothing. When a
comment's latest committed state is a tombstone and the comment is listed again, it
becomes a new present revision bound to that tombstone. That happens even when the
body and `updated_at` are unchanged. A second identical reappearance reproduces that
revision and adds nothing. Each absence or reappearance therefore appends one record
per change of state. Journal order records the reader's observations, not a
deletion chronology.

No tombstone is inferred for comments created outside the window or with unknown
creation time. None is inferred for IDs still listed outside the window or prior
tombstones. HTTP denial, 429, count mismatches, truncated pagination, exhausted
budgets and changed readbacks reject capture before reconciliation. The journal
and checkpoint then stay unchanged. Preview has no committed history and reports
no tombstones. Edits keep their native ID and produce a new present revision
because the body fingerprint and `updated_at` change. Tombstones are excluded
from the v5 relationship graph. The summary reports the window and hash-only
absences with an `absence_is_not_deletion_evidence` warning. Coverage remains
partial.

Synthetic fixtures cover window validation, fingerprint compatibility, journal
separation, edits, absence, idempotence and rejected captures. They do not
establish live GitHub listing semantics, deletion detection or diagnostic
utility. Conditional requests remain open; a `304 Not Modified` must not advance
a checkpoint. `since`-filtered incremental requests, recheck windows and
deletion signals beyond REST listings also remain open in #45.

### Explicit GitHub CLI configuration and native recheck

The CLI accepts a bounded GitHub configuration instead of a local export manifest.
It contains exactly `schema_version: 1`, `source` (a complete API/github SourceSpec),
`endpoint`, `selection` (GitHubSelection.to_dict()) and `limits`. The endpoint is
the explicit GitHub.com repository URL above. `source.root_sha256` must equal
`digest(endpoint)`; its project/scope/version must match the selection and the
independently supplied expected project/scope fingerprints. The identity provenance
is `source_declared_id`. Limits may set only the existing `max_pages`, `max_bytes`,
`timeout` and `seconds` bounds; an empty object selects the same defaults. Unknown
fields, unsafe hosts, unsupported selections and mismatched identities reject
before credentials or network access.

Use `ahl sources probe|preview|import|status --manifest CONFIG --project-sha256 HASH
--scope-sha256 HASH`; import/status require the existing private `--cache` argument.
Probes validate configuration without reading credentials or contacting GitHub.
Cached status is also offline. Preview/import explicitly perform the selected
bounded reads. Enabled public sources may use auth mode `none`. For configured
authorization, pass `--authorization-file PRIVATE_FILE` to each operation that
reads GitHub. This file contains the complete Authorization header value, with an
optional final newline; it must be a bounded single-link regular file. Headers
with embedded control characters, linked files and files over 8 KiB are rejected.
No token, authorization path or response body enters the journal or normal output.
The reader never discovers tokens through the environment, browser or GitHub CLI.

`ahl sources recheck --manifest CONFIG --cache PRIVATE_FILE --project-sha256 HASH
--scope-sha256 HASH` requires an already captured v5 graph and explicitly performs
a fresh bounded capture with the built-in configured GitHub reader. Other registered
collectors cannot grant this capability by returning matching labels. Exact native
source/ID/revision endpoints and relations must match the new observation before a
verified receipt is returned. Stable IDs with stale revisions stay unlinked;
missing targets, unsupported native identities, unobserved relations and declared
conflicts remain explicit. File-based composite identities and cross-reference
events with unknown occurrence identity stay candidates.

The report binds the proposed checkpoint/graph, observed graph/snapshot, current
source specification and resolver version. Verification covers current native
relationships only. Claimed historical intervals are cleared, independent roots
remain unknown, and no task outcome or execution authority is granted. A sequence
of API reads is not an atomic server snapshot. Recheck does not change the journal,
configuration or lock files; failed/limited API reads emit no partial proof set.
Unvalidated metadata labels are represented by the original claim fingerprint,
not echoed as receipt namespaces or resolver labels. Serialized reports cannot be
imported as verified graphs, and stored candidates
are not relabeled. Historical proof persistence, reviewed/cross-source resolution,
grouping and diagnostic-utility validation remain open in #58.

`task test:sources:recheck` runs synthetic configuration, credential and resolver
controls. `task test:sources:github:recheck:live` runs an explicitly selected public
PR import/recheck with no credentials; it uses private temporary metadata, checks
that the candidate journal is unchanged, and is skipped in ordinary test runs.

No source is enabled by installation. Tests use synthetic metadata; they establish
contract validation, checkpoint behavior and a side-effect-free registry, not
real-source compatibility, containment, diagnostic usefulness or certification.
