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
Its separate in-process [harness snapshot binding](controller-sources.md#owned-git-harness-snapshot-binding)
can report that the executor's checked harness input tree equals one clean
local-Git `HarnessSnapshot` captured earlier from the same checkout location.
That receipt is not an `IdentityField`, cannot be imported and leaves
`effective_harness` unknown.
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
fingerprint.
Pinned Claude events may carry [declared skill signals](telemetry.md#declared-skill-signals):
an edit that makes a `Read` path match or stop matching an exact project
`SKILL.md` bundle path, or changes a `Skill` name, changes that derived metadata.
Journal record identities and revisions are unchanged. Collection time remains separate from reported native time.
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
thread ID in this private descriptor. An optional `"max_walk_entries"` field may be
added to change the root walk's visited-entry bound, as described below. Both paths remain normalized relative paths
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
stable-input lifecycle. The root walk is iterative and has two bounds: 128
directory levels below `source_root` and, by default, 250,000 visited filesystem
entries of any kind (files, directories, links and special files, related to
rollouts or not). The bounds target pathological trees, not ordinary large stores.
The optional descriptor field `max_walk_entries`, a positive integer of at most
5,000,000, replaces the entry default; `0`, negatives, booleans, `null`, other
types or larger values reject the operation (exit 2) before scanning. When the
field is absent the selection, its `scope_sha256` and all record identities are
byte-identical to selections written before it existed; when present it is part
of the selection digest and is echoed as `discovery.max_walk_entries`. The depth
bound is fixed. Reaching either bound stops the walk, adds `scan_depth` or `scan_entries` to
`reached_limits` and records a fingerprinted `discovery.walk_limits` receipt with
status `unknown` and reason `scan_depth_limit` or `scan_entries_limit`; the unread
remainder is unknown coverage, never an empty or successful scan, and the
checkpoint is blocked. Trees within both bounds produce unchanged summaries,
records and fingerprints. There are no implicit byte, line, time or
collection-count ceilings. Optional descriptor fields
`max_files`, `max_scan_files` and `max_records` preserve caller-selected limits;
omission or `null` means unlimited. Coverage declares the selected record/file
limits and actual counts; `native_summary.bytes_read` reports cumulative reads
without declaring an aggregate byte bound. The root mapping version is
`codex-native-root-journal-v1`.

Preview exposes `checkpoint_ready: false` for any selected partial tail, reached
file/scan/walk cutoff, unsupported header, skipped unsafe entry or empty accepted scan.
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
native record identity and history. Merged files keep offset `capture_order` values
but each file is a separate `source_order_segment`; the order between files,
including an archive and its active copy, is not established. Analysis treats the
capture order of a case whose records span segments as unknown; only valid strictly
increasing timestamps can still order it, and the receipt counts those cases
separately. Segments and subordinals are
excluded from record identities, revisions and duplicate-UUID comparison. Main and subagent writers remain distinct
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
Object-store files are copied into the private view, and commit patches and the
Git binary are hashed, in fixed-size chunks, so capture memory does not grow with
pack, loose-object or patch size. Commit, tree and selected harness contents are
still read whole.
Source filesystem operations are checked between reads but cannot be preempted
at an exact optional deadline. Links,
nonregular files, alternate object stores, bare repositories, reftable repositories
and selected submodule/nested-repository contents are blocked without fallback.
All observed source metadata are rechecked before returning; failed reads leave
the committed journal untouched. Private copies are removed after success or error.

Each successful built-in read also exposes `GitExport.harness_capture`, an opaque
handle valid only in the reading process. It privately keeps the selected paths'
content/mode facts and location fingerprints (normalized absolute path plus
device/inode) for the checkout and the selected paths' ancestor directories, plus a
bounded listing of hashed relative file paths in each of those directories. The
snapshot, summary, records and journal are unchanged; a subclass reader gets
`None`, and there is no public issuance function. Only the [controller harness binding](controller-sources.md#owned-git-harness-snapshot-binding)
consumes the handle. A renamed or copied checkout, a journal checkpoint or a
serialized snapshot cannot stand in for it.

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
fallback names in order. Following `openai/codex@d786ff91`
`codex-rs/core/src/agents_md.rs`, the first candidate that exists as a regular file
is selected by its metadata alone, whatever its content. A non-empty selection is
declared applicable. A whitespace-only or zero-byte selection is hashed and stays
`unknown` with reason `selected_empty`: it contributes no guidance, uses no
`project_doc_max_bytes` budget and takes no `parent` edge, yet it still shadows the
directory's later candidates. Every later existing candidate in that directory,
empty or not, remains visible as `shadowed` (`directory_precedence`) with a
`shadows` edge from the selection, so an empty `AGENTS.override.md` hides that
directory's `AGENTS.md`. Only `CODEX_HOME` (below) skips empty files. Selected
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
  a non-empty UTF-8 `CLAUDE.md` and `.claude/CLAUDE.md` are
  `declared_applicable` with reason `directory_concatenation`. They are
  concatenated, not shadowed. `CLAUDE.local.md` is always `private_excluded`
  (see the whole-tree section below). A whitespace-only launch file is hashed but stays
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
- **Explicitly unknown.** Selected `.claude/rules/` files stay `unknown`. Their
  reason names the rule's frontmatter (`rule_without_paths`,
  `rule_conditional_paths`, `rule_frontmatter_unsupported`, `rule_blocked`,
  `rule_not_utf8`, or `rules_tree_not_markdown`); the
  [path-scoped rules receipt](#declared-claude-code-path-scoped-rules-and-codex-exec-policy)
  classifies them. Launch filenames below `task_cwd` (`on_demand_subdirectory`)
  also stay unknown.
  Files below `task_cwd` are discovered by the whole-tree walk described below.
  Managed and user files, approval decisions, the session's on-demand
  truncation point and path-scoped rule matching are not captured. A selected
  `AGENTS.override.md` or other unrecognized file in the chain is `unsupported`.

An import from the launch chain whose path crosses a symbolic link, a
non-directory component or a nested repository fails the capture, as does a
linked final target or a non-UTF-8 launch file. Only a genuinely absent path is
`import_missing`. Every file is hashed in full with no byte cap, but it is
streamed: no file's bytes are kept once its hash is taken. A file that is
expanded for imports is read again, at most 4 MiB of it, and its hash is
checked against the first read. Node count is bounded by the launch
candidates, the instruction-named entries found by the whole-tree walk (itself
capped at 100000 entries), one node per unreadable directory, the distinct
in-checkout files reached within four hops, up to eight unresolved-reference
nodes per expanded file, one per unresolved reason, and the selected files. The
receipt uses the existing `InstructionInventory` shape and `instruction_inventory`
record. Observed loading stays `unknown`, and the receipt grants no edit authority.
Content, import, cwd and profile changes invalidate it. Native
`InstructionsLoaded` evidence, managed and global capture, rules and on-demand
modeling, and execution binding remain open in #46.

### Whole-tree instruction inventory and declared instruction settings

The Claude Code profile walks the whole checkout, not only the caller's selected
files. Each directory is opened by name beneath an opened directory with
`O_NOFOLLOW`; `.git` and nested repositories (a directory holding `.git`) are
skipped. Every `CLAUDE.md`, `.claude/CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`
and `.claude/AGENTS.md` becomes a node, classified against `task_cwd`:

- **Launch ancestors** keep the labels above.
- **Below `task_cwd`**, `CLAUDE.md` and `.claude/CLAUDE.md` are `unknown` with
  reason `on_demand_subdirectory`. The documented trigger is a read, write or
  edit of another file in that subtree. A subdirectory `AGENTS.md` is on demand
  only when that directory has none of the three `CLAUDE.md` files. Otherwise it
  is `shadowed` by the first of them, with the same assumed or declared reason as
  a launch `AGENTS.md`. The existing `@path` resolver also expands every
  on-demand file. Files reached only that way stay `unknown`
  (`on_demand_import`); missing, cyclic and unsafe imports get the usual labels.
  An on-demand import that crosses a link, a non-directory or a nested
  repository, names a linked final target or cannot be read becomes an
  unresolved node (`import_crosses_link_or_repository`,
  `import_target_link_not_followed` or `import_target_unreadable`) instead of
  failing the capture.
- **On-demand size.** The public memory documentation
  (code.claude.com/docs/en/memory, "Claude ignores the end of my subdirectory
  CLAUDE.md", checked 2026-11-05) says a subdirectory `CLAUDE.md` or
  `CLAUDE.local.md`, and each file it imports, is truncated, not skipped, past a
  per-file limit of 40,000 characters (200K context window) or 150,000 to
  200,000 characters (1M window). The session's window is never declared here,
  so the 4 MiB launch skip is not applied on demand. An on-demand file or
  import longer than 40,000 characters keeps its imports but gets reason
  `on_demand_truncation_unverified`. An import token ending past character
  40,000 is uncertain (`import_beyond_on_demand_truncation_unverified`), and its
  target keeps that reason unless another path reaches it. Only the first
  4 MiB of an on-demand file are scanned for imports, which is past every
  documented limit.
- **Anywhere else** (the same filename in an unrelated scope), files are
  `out_of_scope` with reason `outside_task_scope`.
- **`CLAUDE.local.md`** is personal and usually gitignored. Every copy, wherever
  it sits, is `private_excluded`: it is hashed, never admitted, expanded, staged
  or imported, and its bytes never reach output. A present copy still counts as
  a `CLAUDE.md` when deciding whether an `AGENTS.md` is shadowed, as documented.
- A linked or hard-linked instruction file outside the launch chain is
  `unsupported` (`instruction_link_not_followed`) and is never opened. Links in
  the launch chain still fail the capture, including a launch-chain import of a
  linked or unreadable file the walk already recorded.
- An empty or whitespace-only `CLAUDE.md` or `AGENTS.md` in the launch chain
  stays `unknown` (`empty`) under any `instruction_files` mode that would
  otherwise admit it.

Two optional profile fields declare settings that live only in user or managed
configuration:

- `instruction_files`, the **Project instructions** value. With
  `claude-md-or-agents-md`, a launch `AGENTS.md` is `declared_applicable`
  (`declared_instruction_files_setting`) when no launch `CLAUDE.md` file exists.
  Otherwise it is `shadowed` with that reason. With `claude-md-and-agents-md`
  it is applicable next to them. With `claude-md` every `AGENTS.md` is
  `out_of_scope` (`excluded_by_declared_instruction_files`). `managed-only`
  also excludes the launch `CLAUDE.md` files and leaves subdirectory files on
  demand. Any other label is an unknown mode: no project node is applicable
  or shadowed, and each one stays `unknown` with reason
  `instruction_files_setting_unknown`. Without the field the assumed-default
  labels above apply.
- `claude_md_excludes`, a list of up to 256 glob patterns. The runtime matches
  them against absolute paths. Here they are matched against the
  checkout-relative path, with and without a leading `/`. A matching file
  stays `unknown` with reason `claude_md_excludes_match_unverified`.

The walk is bounded at 100000 entries and depth 64. Going past either bound
gives a `blocked` receipt with a `tree_enumeration_bound_exceeded` node. It is
never reported as a complete enumeration. These bounds limit the walk, not the
bytes hashed: memory use stays bounded because files are streamed, as described
above.

A subdirectory that cannot be opened, listed or stat'ed gets an `unknown` node
with reason `instruction_directory_unreadable`, and an unreadable instruction
file found by the walk keeps its label with presence `unknown` and reason
`instruction_file_unreadable`. Neither fails the capture. The receipt is
`blocked` only when the entry is `task_cwd`, one of its ancestors or below it,
where it can hide applicable or on-demand files. An unreadable entry in an
unrelated scope leaves the receipt `declared`. An unreadable checkout root or
launch-chain file still fails the capture.

The Codex profile keeps its directory precedence and adds three declarations,
pinned to the public `openai/codex@d786ff91` source
(`codex-rs/core/src/agents_md.rs` and
`codex-rs/codex-home/src/instructions/mod.rs`):

- `capture(..., codex_home='home')` names a folder-relative `CODEX_HOME` that
  does not overlap the checkout. It is never discovered, defaulted or read from
  the environment. Its `AGENTS.override.md`, then `AGENTS.md`, are hashed with
  origin `codex_home`. The first non-empty one is `declared_applicable`
  (`codex_home_first_non_empty`), so an empty override falls through, unlike a
  project directory, where an empty selection shadows the rest. A later
  non-empty file is `shadowed` (`codex_home_precedence`). A `parent` edge links
  it to the first applicable project file. It is not counted against
  `project_doc_max_bytes`.
- `project_root_markers` (default `['.git']` in Codex; undeclared here keeps
  the checkout root as the project root). Discovery starts at the nearest
  directory from `task_cwd` upward that holds a marker. Directories above it
  are `out_of_scope` (`outside_project_root`). An empty list limits discovery to
  `task_cwd`. A declared marker found nowhere in the checkout leaves the
  ancestors `unknown` (`project_root_marker_not_found_in_checkout`), because a
  marker above the checkout is not read. Those ancestors may still load, so the
  selected (first existing) file of each counts toward a declared
  `project_doc_max_bytes` unless it is empty; a later file that would pass the
  limit is `unknown` (`byte_limit_requires_native_resolution`) and the receipt is
  `blocked`.
- `project_trust: declared_untrusted` drops every present project file to
  `out_of_scope` (`explicitly_untrusted`), as Codex loads no project docs for an
  untrusted project. The `CODEX_HOME` files still apply. Trust is never read
  from configuration.

The documented 32 KiB `project_doc_max_bytes` default is reported as a fact. It
is not applied as an implicit bound: native partial truncation is still not
modelled, and a declared bound that is exceeded still blocks. Unsupported
runtime/version pairs return `unsupported` before anything, `CODEX_HOME`
included, is read.

The surface inventory registers `instructions.parse_surface` for the
`instruction` kind, and `surfaces.RULES` classifies nested `CLAUDE.md`,
`AGENTS.md` and `AGENTS.override.md` files as `recommendation_only`
instructions, so nothing can adopt them yet. Under Claude Code,
`CLAUDE.local.md` is `private_excluded` and its content is not read. Parser facts are
labels and counts only: `checkout_relation` (`launch_ancestor`,
`on_demand_subdirectory` or `outside_task_scope` for Claude Code;
`directory_chain`, `below_task_cwd_not_loaded` or `outside_task_scope` for
Codex), import token counts, `loading_trigger`, `subdirectory_size_limit:
unverified`, `instruction_files_setting: instruction_files_setting_unknown` and
`exceeds_default_project_doc_max_bytes`. `checkout_relation` assumes a
checkout path; the node's origin and root mark a declared external root. None
of these facts is loading evidence or a task outcome.

### Read-only harness surface inventory

`ahl sources inventory CHECKOUT --runtime RUNTIME --version VERSION` lists the
harness surface files in one checkout. An optional `inventory_profile` in the Git
selection captures the same inventory during a Git read:

```json
{"schema_version": 1, "runtime": "codex", "version": "0.166.0", "task_cwd": "src/task"}
```

The runtime, version, `task_cwd` and optional `project_trust`
(`declared_trusted` or `declared_untrusted`) are caller declarations. Trust
defaults to `unknown` and is never read from user configuration, so an
unreadable or unread config never makes a project trusted or untrusted. A
version other than the `surfaces.PINS` pin returns an `unsupported` inventory
with no nodes. `task_cwd` must be a directory reached by the walk.

The walk opens the checkout with `open_directory_nofollow`, then opens each
directory and file by name beneath an already opened directory with
`O_NOFOLLOW`. These entries are refused and never followed or read: symbolic
links (`symlink`), FIFOs, sockets and devices (`special_file`), the checkout's
`.git` (`vcs_metadata`), any directory that contains a `.git` entry
(`nested_repository`) and multiply linked surface files (`hardlink`). Each
regular file is classified by `surfaces.classify`; `unsupported` files are not
opened. Nothing is executed, written or imported from the checkout.

Each node records its relative path, `path_sha256`, kind, scope directory,
content SHA-256, permission bits, `declared_state`, `parse_status`, trust and a
capability reference such as `instructions:codex`, which names the matching
`surfaces.CAPABILITIES` rows. `path_sha256` is the `HarnessFile` path identity,
and `harness_sha256` names the `HarnessSnapshot` captured in the same Git read.
The inventory therefore extends that snapshot without changing its fingerprint
or the controller binding. Files that classify as `private_excluded` are
`present_content_not_read`: their bytes are never read or hashed.
`observed_loading` and `effective` are always `unknown`. Construction rejects
any other value, because a listed file is not evidence that a runtime loaded it.

Parsers are registered in `PARSERS`, a tuple sorted by surface kind with one
self-contained `(kind, capability surface, 'module:function')` line per kind.
Parsers not described in this document are stubs that return `not_parsed`. A kind with
no entry is `no_parser`, and a parser that raises `ValueError` gives
`parse_failed`. Parser facts are flat scalar mappings, so raw text never
reaches a node.

Bounds: 1000 nodes, 10 MiB of read candidate bytes across all roots, directory
depth 32, 20000 examined directory entries and 50000 parser facts across all
nodes, so many small parsed files cannot multiply memory. Entries are counted as each is
read, so one huge directory is blocked before it is held in memory. Exceeding
any bound returns `blocked` with its reason and no nodes or refusals, never a
truncated result.
Roots outside the checkout, such as `~/.claude`, `$CODEX_HOME`, plugin caches
and managed paths, are never read implicitly. The caller can declare one with
`--external-root LABEL=PATH`. It must be an absolute path that does not overlap
the checkout, its nodes are labelled `caller_declared_external` with trust
`unknown`, and it is only read.

The CLI writes the full inventory document to a private `0700` directory
under `$XDG_STATE_HOME/agent-harness-lab/inventories/`, in a `0600` file
marked `publication_allowed: false` and `loading_evidence: false`. It prints
path-free metadata only. Git summaries carry only the inventory fingerprint,
status and counts. Exit 0 means declared, 2 means invalid input, and 3 means
blocked or unsupported. Synthetic tests check the walk, the refusals, the
bounds, unchanged trees and the absence of processes and network use. An
open() audit compares inodes, because opens beneath a directory descriptor
carry no absolute path: every opened or listed descriptor must be the
checkout, an entry beneath it or one of its ancestors. Other surface parsers
and loading attribution remain open in #275 to #282.

### Declared Claude Code path-scoped rules and Codex exec policy

`surfaces.classify` names two rule kinds that never mix. For `claude-code`,
every `.md` file below a `.claude/rules/` directory, including a nested one such
as `pkg/.claude/rules/`, is kind `path_rule`. The row outranks every skill row, so
`.claude/rules/skills/x.md` or a rules tree below a `skills/` directory is still a
rule and carries the `skill` facet. Inside an adoptable skill bundle the file
keeps the bundle's writability. For `codex`, `.codex/rules/*.rules` is kind
`exec_policy`, and so is `rules/*.rules` at the top of a caller-declared
external root such as `$CODEX_HOME`. The inventory passes
`external_root=True` only for those roots, so a checkout's own top-level
`rules/` directory stays `unsupported`. Neither kind carries the `instruction`
facet, and neither runtime classifies the other's files. `path_rule` stays
non-writable until adoption is extended; `exec_policy` is `recommendation_only`,
because it is permission policy.

The rules follow the public [Claude Code memory documentation](https://code.claude.com/docs/en/memory)
and [Codex rules page](https://developers.openai.com/codex/rules), retrieved
2026-11-05 for the `surfaces.PINS` versions. `agent_harness_lab.sources.claude_rules.capture(checkout, profile,
task_paths=..., setting_sources=...)` takes a `claude-code` inventory profile,
optional declared task file paths relative to the checkout, and an optional
declared `--setting-sources` list. It runs the read-only harness inventory, then
re-reads each in-checkout rule through the no-follow walk. If the bytes differ
from the inventory digest, the capture fails. User rules in `~/.claude/rules/`
are not read.

Frontmatter goes through `frontmatter.string_lists`, the strict mode of the
bounded `sources/frontmatter.py` reader that agent definitions also use. It is not
a YAML parser. It accepts only a leading `---` block of at most 200 lines and
64 KiB whose one key, `paths`, holds a YAML block list or a one-line flow sequence of plain,
single-quoted or double-quoted strings, up to 256 items. Any other construct is
`frontmatter_unsupported` and is never guessed. That includes other keys, the
documented comma-separated string form, escapes, anchors, tabs, trailing
comments, an unterminated block and an opening delimiter that is not exactly
`---`: one after a byte-order mark, with trailing blanks, or after blank lines.
Claude Code loads a rule whose YAML does not parse as if it had no `paths`.
This reader cannot tell that case from its own unsupported subset, so such a
rule stays `unknown`. Non-UTF-8 rules are `not_utf8`.

Brace groups expand up to 256 patterns per rule, and patterns without braces do
not count. A rule past that bound, or with a pattern longer than 4096
characters or of more than 64 path segments, is `blocked`. Brace groups are found
in one linear scan, and matching walks the pattern segments iteratively, so it
never exhausts the stack. Matching draws on one budget of 10,000,000 steps for
the whole receipt, shared by every rule: one step per pattern checked and one
per segment comparison. An unverified pattern is decided once and never paired
with task paths, so it costs one step. When the budget runs
out, the receipt is `blocked` with `match_budget_exceeded` and lists no rules,
so many small hostile rules cannot add up to unbounded time. The documented
runtime budget is 1000 patterns, so `blocked` means AHL did not evaluate the
rule. It does not mean the runtime skips it. Matching follows the documented
examples. Patterns are relative to the directory that contains the rule's
`.claude` folder. A leading `/` anchors a pattern, a pattern without a slash
matches at any depth, and `**` spans whole path segments. Other segments use
`fnmatch`. The capability table marks
dotfile, case, bracket and negation semantics `unverified`, so each pair is
matched twice. A match counts only if it holds when wildcards never consume a
dot-leading segment, and a miss counts only if it holds when `*` may consume
one and case is ignored. Any other pair is unknown. A pattern with `[`, `]`,
`\`, `!`, a brace left after expansion, a `.` or `..` segment, or a trailing
`/` is never treated as a match or a miss.

Each `RuleNode` gets one classification:

- `skipped_setting_sources`: declared setting sources lack `project`, which the
  runtime honours from 2.1.211.
- `launch`: no `paths`, and the base directory is the checkout root or a
  directory between it and `task_cwd`. Ancestors other than the root are
  assumed by analogy with `CLAUDE.md` and are `unverified`.
- `on_demand_subdirectory`: no `paths`, and the base directory is below
  `task_cwd`.
- `outside_task_scope`: no `paths`, and the base directory is neither an
  ancestor of `task_cwd` nor below it, such as a sibling directory.
- `conditional_matched` or `conditional_unmatched`: a declared task path below
  the base directory matches, or none does.
- `conditional_unknown`: no task paths were declared, or no match is certain
  and the outcome of some pair rests on unverified pattern or glob semantics.
- `unknown`: the frontmatter is unsupported, blocked or not UTF-8.

Symbolic links in a rules tree, including a linked `.claude` or `rules`
directory, are refused as `symlink_not_followed`. Claude Code follows them, so
those rules stay out of the receipt. Hard-linked rules are refused as
`hardlink_not_read` and special files as `special_file_not_read`. No rule is
ever reported as loaded. `observed_loading` and `effective` are always
`unknown`, and the document carries `loading_evidence: false`,
`adoption_authority: false` and `publication_allowed: false`. Without declared
setting sources, it records `setting_sources_origin: assumed_default`.

The `path_rule` inventory parser records only `rule_base`, `rule_parse`,
`rule_patterns` and `rule_loading`. `rule_loading` is `conditional_unknown` for
any rule with `paths`, because the inventory profile declares no task paths.
Codex `.rules` files have no parser. They get one bounded read and a SHA-256
digest, with `parse_status: no_parser` and the declared project trust. They are
never evaluated as Starlark, and no process is started. `codex execpolicy check`
is the native validator, recorded in the capability table. Synthetic tests count
the opens of the policy file and deny process creation and `exec`, `eval` and
`compile`. Rule rewrites and adoption remain open in #283 and #285.

### Static hook inventory

`sources.hooks_inventory.capture(CHECKOUT, PROFILE)` takes one harness
inventory and returns the hook registrations it declares. It does not read
any file a second time. Hook commands, scripts and configuration values are
never executed, imported or spawned. Script bytes are only hashed by the walk.
The schemas follow the `surfaces.PINS` pins: the
[Claude Code hooks reference](https://code.claude.com/docs/en/hooks) at 2.1.317
and [openai/codex@d786ff91](https://github.com/openai/codex/tree/d786ff91/codex-rs/hooks/src/engine/discovery.rs)
at 0.166.0, both retrieved 2026-11-05.

The parser reads these files: Claude `.claude/settings.json` and plugin
`hooks/hooks.json`, plus Codex `.codex/hooks.json` and the `[hooks]` table of
`.codex/config.toml`, which is decoded with `tomllib`. `.claude/settings.local.json`
is `private_excluded`. It is listed as `present_content_not_read` and is never read or
hashed; reading it on an explicit opt-in remains open. In `surfaces.classify`,
the Claude settings file and the two Codex files are `hook` surfaces with
`contract_required` writability, and the Git hook managers are
`recommendation_only`. Only the `hooks` key is
projected. All other settings keys, such as permissions, env and MCP servers,
appear only as a count and a digest of their key names. JSON is decoded with
`hardened.load_json`, and decoded TOML passes through the same depth bound. A
file over 1 MiB or with more than 512 handlers, a malformed file, invalid TOML
or a file nested past the depth bound is `parse_failed`, never truncated. Each handler
adds 15 facts to the harness inventory, so the inventory-wide 50000-fact bound
also limits the handlers summed across files. Many small plugin `hooks/hooks.json`
files that exceed it make the whole inventory `blocked` with `fact_bound_exceeded`.

For each handler the inventory records the event, checked against the 33
pinned Claude events or the 14 Codex events; an unknown event name is flagged
and not recorded. It also records the matcher kind, the handler type, the
timeout and `onFailure` (Claude `command` and `http`, gated at 2.1.295). The
matcher text is kept only as a digest. Claude matchers are classified with the
documented rule: `*`, empty or omitted is `all`, a plain token is `exact`, a
`|`- or `,`-separated list is `pipe_list`, and anything else is `regex`. Codex
matchers are `regex`. Codex `prompt` and `agent` handlers are
`parsed_but_skipped`, because the pinned discovery skips them. Fields outside
the pinned handler schema are counted as unknown. Commands, env, headers, MCP
input and prompt text are never recorded; commands are kept only as a digest,
and header and env entries keep
only a digest of their key names.

Warnings cover `unknown_event`, `unknown_field` (including a Codex
`hooks.json` top-level key, which `deny_unknown_fields` rejects),
`plugin_hooks_wrapper_missing`, `unknown_handler_type`, `parse_failed`,
`private_excluded_not_read`, `all_hooks_disabled` and
`hooks_json_and_toml_in_one_layer`. `all_hooks_disabled` is raised when a Claude
settings file sets `disableAllHooks: true`; its handlers are still listed as
declared, not active. The last one is raised when one Codex layer has handlers in
both representations.
Handler trust is always `unknown`. `[hooks.state]` `trusted_hash` entries are
counted, but mapping one to a handler is unverified at the pin.

Command resolution tokenizes the command with `shlex`, or uses the exec-form
`args` as given. Only `${CLAUDE_PROJECT_DIR}` (Claude only) is substituted, with the
checkout root, giving the `project_dir` anchor. Any other relative path is
`cwd_relative_unverified`. A leading interpreter such as `bash` or `python3` is
skipped only when the next token is not an option. Any interpreter option, such as
`-c`, `-lc`, `-ec`, `-m` or `--eval`, can turn the next token into inline code, so
the command is `external_or_dynamic_command` and the code string is never
recorded. A target containing whitespace or a control character is a quoted
argument string, not a path, and is also external. A leading `NAME=value` or
`NAME+=value` environment assignment or a `#` comment is never taken as the
program, so the command is external and the assignment value, which may be a
secret, is never recorded. A safe repo-relative
path containing `/` becomes a `referenced_script` node with the content hash from
the inventory, or `inventoried: false` when the walk did not list it. A pipe, a
substitution, a bare `PATH` name, an absolute or parent path, or any other variable
gives `external_or_dynamic_command`. Script nodes are `native_runtime` when a
parsed native handler references them. Git hook managers (`lefthook.yml`,
`.pre-commit-config.yaml`, `.husky/*`, `.git/hooks`) are `git_developer`, and
other hook-path files are `ordinary_script`.

`observed_execution` is always `unknown` and `executed` is always `false`;
construction rejects any other value. Configuration presence is not evidence
that a hook ran. Skill and agent frontmatter hooks, a CLI, and loading or
execution attribution remain open in #276, #278 and #282.

### Static Claude workflow script lint

`sources.workflows_inventory.capture(checkout, profile, personal=None, plugins=None)`
lists Claude Code workflow scripts and lints them statically. The lint is also
registered as the `workflow` parser in the harness inventory. `surfaces.classify`
names each direct `*.js` child of `.claude/workflows/` at any depth a `workflow`
surface that is `contract_required`, for `claude-code` only. The `workflow` kind
outranks the skill and hook path rules, so a `.claude/workflows/` beneath a
`hooks` or `skills` directory still yields workflow entries. No script is ever
executed: no `node`, `deno` or other JavaScript runtime, no subprocess, no import
and no evaluation. Each script is read as bytes through the harness inventory walk,
then scanned by a bounded standard-library lexer that skips comments, strings and
template text. Every entry and fact carries `lint:
static_lint_not_runtime_validation`, because a heuristic lint is not runtime
validation.

The rules follow the pinned workflows page,
<https://code.claude.com/docs/en/workflows>, at Claude Code 2.1.317 (retrieved
2026-11-05), as recorded in `surfaces.CAPABILITIES`. The lint checks the following:

- `meta` is `meta_first_literal` only when the first statement is
  `export const meta = {` followed by a plain object literal. That literal may
  hold only string, number, boolean and null values, arrays and nested objects,
  and it needs `name` and `description` string literals; a number, boolean or
  `null` in either is not one. A preceding import or
  directive, a variable, call, spread, shorthand or computed key, or a following
  operator gives `meta_not_first_literal`, which means the command would vanish
  from `/` autocomplete.
- It counts `import(` (`forbidden_dynamic_import`), `Date.now(`, `Math.random(`
  and a `new Date` with no arguments in code, including template
  interpolations. Matches inside strings and comments are not counted. A
  `new Date(value)` with an argument is allowed, as the page states. A static
  `import` statement is counted as `static_import`, because the page forbids
  module loading.
- It records `phase()` calls, the titles listed in `meta.phases` and
  `phase_titles_unlisted`. An unlisted title is informational, not an issue.
- The meta name is kept only as `meta_name_sha256`, so raw script text never
  reaches a fact.

`lint_result` is `issues_found`, `no_issues_found` or `lint_inconclusive`; it is
never `pass`. The lexer tells a regular expression from division by the
preceding token. A `/` starts a regular expression after an operator, an opening
bracket, a template interpolation, an expression keyword or the `)` that closes
an `if`, `while`, `for`, `for await` or `with` head. It is division after any
other `)`, after `]`, a name, a number, a string or a keyword used as a property
name. After `}`, `++`, `--` or a bare `of`, which may also be a variable name, the
preceding token cannot settle it, so the script is `regex_or_division_ambiguous`.
These cases give `lint_inconclusive` with an `inconclusive_reason`: an
unterminated comment, string, template or regular expression, unbalanced
brackets, `regex_or_division_ambiguous`, an `invalid_escape` such as
`\u{110000}`, `\u` or `\x` without its hex digits, an `identifier_escape` (a
backslash in code, such as `D\u0061te.now()`), a template literal in `meta`,
invalid UTF-8, or more than 200000 tokens or 64 open brackets. The call and phase
counts are then `null`, never `0`. A malformed script never fails the whole
inventory; it is listed as `lint_inconclusive` and `name_unavailable`. A clean
lint is not evidence that the script parses, runs or loads.

Project scripts come from every `.claude/workflows/` on the path from `task_cwd`
up to the checkout root; directories off that path are left out. `personal` is
the absolute path of a personal workflows directory, such as
`$CLAUDE_CONFIG_DIR/workflows`. `plugins` maps plugin names to absolute workflows
directories. Both are caller declarations, are never discovered, must not
overlap the checkout, and are opened without following links. A linked,
missing or non-directory root is labelled `refused_link`, `absent` or
`not_a_directory`. Custom plugin manifest `workflows` locations are not read.

Each entry's `state` follows the documented precedence. The closest directory
wins a name, a project script shadows a personal one with the same name, and
plugin scripts are namespaced by plugin. Two same-name scripts with no
documented order between them, such as two files in one directory, are
`clash_unresolved`, never a guessed winner. A script without a literal name is
`name_unavailable`. When its lint was inconclusive or its parse failed, it may
still carry the same name, so a leader it could outrank or tie is
`clash_unresolved`, not `precedence_winner`. A `meta_not_first_literal` script,
which the page says drops out of `/`, is left out of that doubt. Clashes with
bundled workflows are not modelled.
`observed_loading` is always `unknown`, and documents carry `executed: false`,
`loading_evidence: false` and `publication_allowed: false`. Symlinked, special
and multiply linked scripts are refused. Harness inventory bounds block the
whole result instead of truncating it.

Codex has no equivalent. A `codex` profile returns `unsupported` without
reading anything, and its `evidence` cites the `surfaces.CAPABILITIES` row for
openai/codex@d786ff91, whose pinned `codex-rs` source has no workflow script
loader. The absence of a native static validator stays `unverified`. The
version gates recorded for this release are the `/workflow-authoring` skill
(>=2.1.248) and `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` (>=2.1.269).

### Declared custom agent definitions

`agents_inventory.capture(checkout, profile, external_roots=..., roles=...)`
runs the harness inventory and joins the `agent` nodes into declared
definitions. It never loads, spawns or validates an agent with a runtime. The
`surfaces` rows classify Claude `.claude/agents/**/*.md` and Codex
`.codex/agents/**/*.toml` at any depth, including below a folder named
`hooks` or `skills`. They also classify the layer-relative
`agents/**` of a caller-declared root, which is labelled
`not_a_runtime_location` inside the checkout. Agent files can embed hooks, so
every agent row also carries the hook contract facet. An agent edit is
therefore never adoptable and stays in a plan's `unresolved_operations`.
`roles` maps an external root label to `claude_user`, `claude_plugin` or
`codex_home`. A root without a role gives `undeclared_root_role`.

Field sets are pinned to the `surfaces.CAPABILITIES` sources: Claude Code
2.1.317 [sub-agents](https://code.claude.com/docs/en/sub-agents) and Codex
0.166.0 `openai/codex@d786ff91`, namely `codex-rs/agent-roles` and
`RawAgentRoleFileToml` over the flattened `ConfigToml` in
`codex-rs/config/src/config_toml.rs`.

Claude frontmatter is read by `sources/frontmatter.py`, a bounded reader
(64 KiB, 512 lines) for a small YAML subset. The opening fence must be on
line 1. These Claude files are `skipped_silently` with a reason:
`no_leading_fence`, `bad_yaml`, `missing_name`, `invalid_name` (a leading `-`,
a `:` or more than 256 characters) and `missing_description`. The subset
covers plain and quoted keys, quoted scalars with a trailing comment, flow
lists and block lists indented or at the key's own indent. `bad_yaml` is kept
for syntax every YAML parser rejects: a duplicate key, tab indentation, an
unclosed quote or flow list, text after a closing quote, `: ` inside a plain
scalar, and a bare word or stray list entry where a key belongs. Other YAML
outside the subset, such as anchors, tags, flow mappings, nested flow
collections and an indented root, gives the disposition `unknown` and is
never guessed. A `#` inside a quoted flow item or a `|`/`>` block scalar is
content, not a comment, and comment detection is one linear scan. A column-0
line inside a block can only be a comment, so it ends a block scalar, and an
indented non-comment line after the end gives `unknown`. Unknown
fields give `ignored_by_runtime`.
`omitClaudeMd` (2.1.271) and `autoCompactWindow` (2.1.296) are checked against
the declared version, and the gates come from the capability rows. For a
plugin agent, `hooks`, `mcpServers`, `permissionMode` and `initialPrompt` give
`ignored_for_plugin_agents`. A plugin agent with no name or with bad YAML is
declared under its file name, as documented. A plugin file without a leading
fence stays `unknown`. A definition whose disposition is `unknown` gets the
precedence `unknown_definition`. When it is in reach (in the `task_cwd`
ancestry and not trust gated), it may be the file the runtime loads, so a
selection it ranks equal to or above becomes `depends_on_unparsed_definition`
instead of `selected_by_declared_precedence`. That covers any name when the
unknown file has no readable name, and the same name otherwise. Declared precedence follows the documented order:
project, then user, then plugin. Only `.claude/agents` directories at
`task_cwd` or its ancestors count, and the nearest one wins. Others are
`outside_task_cwd_ancestry`. Same-name files in one tree are
`ambiguous_read_order`, because the runtime picks by filesystem read order.

Codex role files are parsed with `tomllib`. A file is `rejected_with_warning`
for `bad_toml`, `unknown_field_rejected` (any key outside the pinned
`deny_unknown_fields` set), `invalid_field_type`, `blank_description`,
`missing_developer_instructions`, `blank_developer_instructions`,
`missing_name` or `invalid_nickname_candidates` (an empty list, or a blank,
duplicate or non-`[A-Za-z0-9 _-]` name after trimming). These follow the
pinned loader's order. `invalid_field_type` covers `name`, `description`,
`nickname_candidates` and every `ConfigToml` field of a primitive Rust type
(string, path, bool, bounded integer, string list or string map). The other
`ConfigToml` fields are enums, structs or maps whose contents are not
modelled. A value of the TOML shape the pinned type reads is accepted: a
string for a unit enum or `ReasoningEffort`, a string or table for
`approval_policy`, a string or string list for `forced_chatgpt_workspace_id`,
and a table for every derived struct or map. Any other value gives `unknown`
(`config_field_type_unverified`) instead of `declared`. Enum values and table
contents are not validated. Nesting deeper than
`tomllib` can recurse gives `unknown` (`toml_nesting_unsupported`) instead of
a crash, because the pinned Rust parser's limit is not modelled. A missing
description stays `unknown` (`description_needs_lower_layer`). The pinned
loader walks layers from low to high and fills it from an accepted same-name
role in a lower layer (`merge_missing_role_fields` in
`codex-rs/agent-roles/src/loader.rs`). When such a role is in the inventory,
the file becomes `declared` (`description_from_lower_layer`), it wins and the
lower role is shadowed. Without one it is `unknown_definition`, even when a
`codex_home` root is inventoried: a lower same-name role whose disposition is
`unknown`, the system layer's agents directory and `[agents.<role>]` tables in
a lower `config.toml` are not modelled, and any of them may complete it. `sandbox_mode`,
`approval_policy`, `model_provider` and `mcp_servers` give
`no_override_effect`. Within one layer the first sorted file wins and later
same-name files are `duplicate_in_layer_warned`. A deeper project layer wins
over a shallower one, and project layers win over `codex_home`. Project trust
is the inventory declaration. It is `unknown` by default, and
`declared_untrusted` gives trust `gated` and precedence `trust_gated`. Codex
loads a project layer only for a trusted project, so with unknown trust the
labels are resolved once as trusted and once as untrusted. A row whose label
differs between the two passes, such as a project role and the `codex_home`
role it would shadow, is `depends_on_project_trust`.

`embedded_hooks()` lists each hook event an agent file declares, with
`classification: hook_contract_required` and `executed: false`. Plugin agent
hooks are listed as `ignored_for_plugin_agents`, so a hook in agent
frontmatter cannot evade hook classification (#80). A file whose fields cannot
be read but which the runtime may still load (unsupported YAML, oversized or
non-UTF-8 frontmatter, too deeply nested TOML; disposition `unknown`) always
gets one hook with the event `unparsed_hooks` and status `unknown`, because a
valid key form such as an escaped or explicit key can evade any text match. A
file the runtime skips or rejects (no leading fence, bad YAML, bad TOML) gets
that hook only when a `hooks` key pattern appears in its bytes. Every definition has
`declared_state: declared`, `available: unknown`, `loaded: unknown` and
`load_timing: unverified`, and construction rejects any other value. Parser
facts are flat label-safe scalars, and names are kept only when label-safe,
with a SHA-256 for duplicate detection. Prompts, descriptions and hook
commands never reach a node. Still open: `[agents.<role>] config_file`
references in `.codex/config.toml`, emission into the hook inventory once it
lands (#276) and loading attribution (#282).

### Commands, skill roots and Codex custom prompts

`commands_inventory.capture(checkout, profile, codex_home=None, codex_config=None)`
runs the harness inventory above and resolves names across its nodes. Each entry
records a kind (`command`, `skill` or `prompt`), an id, a name SHA-256, a path, a
root label, a load class, the content SHA-256, flags and labels. Its
`observed_loading` and `effective` are always `unknown`. Load classes describe
where the pinned documentation says a file would be discovered: `startup`,
`on_demand`, `outside_scan` or `unknown`. They never say that a runtime loaded the
file.

- Claude Code commands are Markdown files below `.claude/commands`, classified by
  the `claude-command` row in `surfaces.RULES`, unless a skill bundle encloses the
  folder: a `<dir>` below `.claude/skills` or `.agents/skills`, or below a root
  `skills/` tree the caller owns. A `.claude/commands` folder below an ordinary
  `skills/` directory, such as `packages/skills`, is still a command folder. Their writability stays
  `unsupported`, and a harness change plan lists a created, edited or removed
  command file under `unresolved_operations`, as it did before the row existed.
  A subdirectory becomes part of the id, so
  `.claude/commands/frontend/component.md` is `/frontend:component`. A command
  whose id matches a skill's id is flagged `shadowed_by_skill`, because the skill
  wins. The registered `command` parser records only flat facts: whether
  frontmatter is present, the description length, and whether `name` or `paths`
  keys appear. Those keys are flagged `frontmatter_name_ignored` and
  `frontmatter_paths_ignored`, since commands do not accept them.
- Claude Code skills in `.claude/skills` in `task_cwd` or any of its ancestors are
  `startup`, named by the frontmatter `name` or else the directory name. Nested
  `<subdir>/.claude/skills` below `task_cwd` are `on_demand` with the same name.
  Only when the skill's directory name clashes with another listed skill is the
  id qualified, as the subdirectory path relative to `task_cwd` and then the
  directory name: with `task_cwd` `apps`, `apps/web/.claude/skills/deploy` next to
  a root `deploy` skill is `/web:deploy`. A skill tree neither above nor below
  `task_cwd` loads `unknown` and has no id.
- Codex skills are read from `.agents/skills` in every directory from `task_cwd`
  up to the checkout root. These entries are `startup`; other roots in the
  checkout are `outside_scan`. A skill without a frontmatter `name` is named
  after its directory, as `codex-rs/skills/src/parser.rs` does at the pin, so
  `.agents/skills/beta` is `$beta`. Skills with the same name are all listed and
  flagged `duplicate_name`, never merged. A `SKILL.md` without a frontmatter
  block is `parse_failed`, and one with an empty or missing `description` is
  `description_missing`; Codex rejects both, so they have no id and join no
  duplicate or name-selected config check. Frontmatter the shared reader cannot
  model is `frontmatter_unsupported` (see below), which claims no rejection. Entries are also flagged
  `name_too_long` (more than 64 characters) and `depth_exceeded`
  (a `SKILL.md` more than 6 directories below the root), following
  `openai/codex@d786ff91` `codex-rs/ext/skills/src/loader/mod.rs`.
  `description_too_long` (more than 1024 characters) carries the
  `description_limit_unverified` label, because that bound was not found at the
  pin. `openai_yaml` records whether the bundle has `agents/openai.yaml`.
- `[[skills.config]]` disables are read only from a caller-declared absolute
  `codex_config` path (at most 1 MiB, parsed with `tomllib`). An entry needs
  exactly one `path` or `name` selector and a boolean `enabled`; any other entry
  is ignored. A skill matched only by `enabled = false` is flagged
  `disabled_by_declared_config`, and a skill matched by both values is flagged
  `config_conflict` instead of having a precedence applied to it.
- Codex custom prompts are read only from a caller-declared `codex_home`, which
  must not overlap the checkout. Only top-level `prompts/*.md` regular files are
  read, using the inventory's no-follow reads and refusals. They continue the
  inventory's own walk, so its entry and byte counts carry on and `MAX_FILES`
  covers the inventory nodes plus every prompt. Each prompt
  has the id `/prompts:<name>`, the load `unknown`, and the labels `deprecated`
  and `loader_unverified_at_head`. Without a declared root, nothing outside the
  checkout is opened, and a checkout `prompts/` folder is not a surface.

`SKILL.md` frontmatter is read again without following links, within the same
byte budget, and must match the inventory node's content hash. A file that changed after the inventory is
refused. Nodes the inventory classes `private_excluded` (for example below
`__pycache__`) were never read, so they are skipped rather than opened.

Emitted ids are derived from paths only. A skill whose frontmatter `name` differs
from its directory name keeps that name in the private shadowing, duplicate and
`[[skills.config]]` checks, but its id is `null` and it is flagged `id_withheld`;
`name_sha256` still identifies it. A Codex name longer than 64 characters has no
id. An id is at most 4096 characters and holds no control characters.
Frontmatter goes through the shared `sources.frontmatter` reader (at most 64 KiB
and 512 lines), so trailing `# comments` are dropped from values. It is never
loaded as YAML. Only failures every YAML parser shares give `parse_failed`: an
unterminated block, a file that is not UTF-8, syntax the reader classes
`bad_yaml` (a duplicate key, an unclosed quote, tab indentation), or a list or
mapping where a `name` or `description` belongs. Valid YAML the reader does not
model (flow mappings such as `metadata: {short-description: s}`, anchors, tags,
an unquoted `argument-hint: [a] [b]`), frontmatter over 64 KiB or 512 lines, and
a `---` fence line with trailing blanks are flagged `frontmatter_unsupported`
instead, because a runtime's own parser may accept them. That flag is an unknown,
not a rejection: a command keeps its path-derived id, while a skill's name and
description were not read, so it has no id, is not flagged `description_missing`
or `name_too_long`, and joins no shadowing, qualification, duplicate or
name-selected config check. A path-selected `[[skills.config]]` entry still
applies to it. The document has `publication_allowed: false`
and `loading_evidence: false`. Its summary carries only counts and the
fingerprint. Codex prompts and the skills config require the `codex` runtime.
User and system skill roots, plugin skills and loading attribution remain open
in later increments.

### Plugin enablement and selected plugin checkouts

`agent_harness_lab.sources.plugins_inventory.capture(CHECKOUT, PROFILE)` reads
the plugin declarations of one checkout for the profile's runtime. It takes the
same profile as the harness inventory. Claude Code declarations come from
`enabledPlugins` and `extraKnownMarketplaces` in `.claude/settings.json`.
`.claude/settings.local.json` is reported as `present_content_not_read` unless
the caller passes `include_local=True`. Codex declarations come from
`[plugins."name@marketplace"]` and `[marketplaces.<name>]` in
`.codex/config.toml`; the `_default` table is skipped. Settings are read as
bounded, link-free files with the strict `hardened.load_json` decoder (or
`tomllib`), and nothing else in the checkout is opened. `tomllib` has no
nesting bound, so a Codex config that exhausts its recursion or nests deeper
than the JSON depth bound of 32 raises `ValueError` (`HostileReport`).

Each declaration records the plugin id, its settings file, the declared
`enabled` value and a resolution. An enabled plugin is `content_not_selected`,
a disabled one is `not_enabled`, and a Codex entry without `enabled` is
`enablement_inherited`. Marketplaces keep only the source kind and a digest of
the declared location, never the location itself. A marketplace from
`.claude/settings.json` applies `after_project_trust`, as the pinned settings
documentation states. A local or Codex project-layer marketplace is
`unverified`, because the pinned Codex source documents `[marketplaces]` as
user-level entries. `observed_loading` and `effective` stay `unknown`.

Plugin content is never looked up. Plugin caches such as `~/.claude/plugins`
and `$CODEX_HOME/plugins/cache` are not discovered, and no `claude plugin`,
`codex plugin` or `git` process and no network is used. The caller can select
a checkout with `plugin_checkouts={"name@marketplace": "/absolute/path"}`. The
same mapping is an optional `plugin_checkouts` key in `ahl.json`, validated by
`config.plugin_checkouts`: at most 32 entries, ids of the form
`name@marketplace`, distinct absolute paths without `..`. A selected id must be
declared enabled. The path must not overlap the analyzed checkout or another
selection, and it must hold a `.git` directory whose `HEAD` resolves, through
a loose ref or `packed-refs`, to a commit. A linked worktree whose `.git` is a
file is rejected.

A selected checkout is walked with the harness inventory walk, refusals and
bounds, which are shared across all selected checkouts. The plugin-root layout
replaces the project classifier: `plugin.json` manifests,
`.claude-plugin/marketplace.json` and `.agents/plugins/marketplace.json`,
`skills/<name>/...`, `commands/`, `agents/`, `hooks/hooks.json`, `.mcp.json`,
`output-styles/` and `workflows/`. Other files are not opened. Each component
uses the parser registered for its kind in `PARSERS`. This increment registers
`plugin` (`parse_manifest`, which records counts and flags only), and a kind
without a parser stays `no_parser` until its parser lands. A plugin-root
`CLAUDE.md` carries `plugin_runtime_loading: not_loaded_by_runtime`, and
`hooks/hooks.json` records `plugin_hooks_wrapper` as `present`, `missing` or
`malformed`, because a plugin hooks file needs a top-level `hooks` key. The
manifest follows the runtime's pinned search order. Claude Code uses
`.claude-plugin/plugin.json`. Codex (`openai/codex@d786ff91`) uses a root
`plugin.json` only when its `$schema` is an agent-plugins schema, then
`.codex-plugin/`, `.claude-plugin/` and `.cursor-plugin/`. A refused candidate
ends the search unresolved.

Each checkout records the HEAD digest and its own `HarnessSnapshot` of the
walked components (committed blobs are not read, so they stay unset), plus an
inventory bound to that snapshot. It is marked `origin: external_repository`
and `writability: recommendation_only`, and construction rejects any other
value. `surfaces.classify(path, origin="external_repository")` returns
`recommendation_only` for every path, walked or not, including paths the
project allowlist would adopt. Only a `.git` or `__pycache__` path stays
`private_excluded`, which is stricter. `plugins_inventory.writability(inventory,
root, path)` returns that verdict for any path below a selected checkout root
and raises `ValueError` for a root that was not selected. `Journal.owned`
rejects their absolute paths. A bound overflow returns `blocked` with no
declarations or checkouts, and the document is marked
`publication_allowed: false` and `loading_evidence: false`. The selected
checkout is never edited, executed or imported.

### Presence-only runtime configuration inventory

`agent_harness_lab.sources.config_inventory.capture(checkout, profile,
declared_roots=...)` records which runtime configuration exists and its
structure, never its values. It takes the same profile as the harness surface
inventory. It opens only fixed paths, by name beneath an opened directory with
`O_NOFOLLOW`, and shares that inventory's byte, entry, depth and row bounds.
For Claude Code the paths are `.mcp.json`, `.claude/settings.json`,
`.claude/settings.local.json` and every `.claude/output-styles/` from
`task_cwd` up to the checkout root. For Codex the path is `.codex/config.toml`.
Roots outside the checkout are read only when the caller declares them by
role. `home` reads `.claude.json`, and only the key names under `mcpServers`.
`claude_home` reads `settings.json` and `output-styles/`. `codex_home` reads
`config.toml`. Any other role label is rejected. Because only those fixed
names are opened, a role root may be an ancestor of the checkout, such as
`$HOME`. A root at or inside the checkout, or one whose fixed path contains
the checkout, is rejected. When a parent component of a fixed path, or
`output-styles` itself, is a link or not a directory, the path gets a
`refused` row, so configuration that could not be captured is never reported
as absent.

The projection is an allowlist. A row field is a fixed vocabulary label, a
boolean, a count or a SHA-256 digest, and construction rejects every other
string. Commands, arguments, URLs, env and header values, bearer variables,
OAuth secrets and instruction text are never recorded. Server ids, env and
header key names and style names are digested. A synthetic test fills every
such field with canary secrets and scans the whole JSON output for them.

- MCP rows record the server digest, transport, the `has_` field names that
  are present and a count of unknown fields. Claude rows record
  `project_approval_prompt` for `.mcp.json` servers and the documented scope
  precedence: local 1, project 2, user 3. Codex rows record `enabled`,
  `default_tools_approval_mode` and the number of per-tool approval overrides.
  Codex precedence is not recorded.
- Output style rows record the name digest, `has_description`,
  `keep-coding-instructions`, `force-for-plugin` and the directory distance
  from `task_cwd`. Among project directories the closest same-named style wins,
  and the others are `shadowed`. Shadowing of user-layer styles is
  unverified, so those rows carry `shadowed: null`. Loading from subdirectories
  of `output-styles/` is also unverified, so those rows carry
  `subdirectory: true`. The `outputStyle` selection in settings is recorded as
  the same name digest.
- The Codex keys `instructions`, `developer_instructions`,
  `model_instructions_file`, `personality` and `compact_prompt` become
  `instructions` rows that hold a value digest.
- Settings rows record key presence for an allowlist that includes
  `permissions`, `env`, `model` and `statusLine`, a count of other keys, the
  `.mcp.json` approval lists as server digests, and Codex `features.memories`
  as `declared_on`, `declared_off` or `default_off`.

Memory locations are `private_excluded`. These are `.claude/agent-memory`,
`.claude/agent-memory-local`, a declared `claude_home/agent-memory`, a declared
`claude_project_state/memory` and a declared `codex_home/memories`.
`.claude/settings.local.json` holds personal settings and is `private_excluded`
too, as in `surfaces.RULES` and the hook inventory. Existence comes from
`lstat` on the parent descriptor, and the location is never opened, listed or
digested. An inode-level open audit enforces this even for declared roots.
Every other row is `recommendation_only`, and `surfaces.RULES` gives the same
writability to these paths. In `surfaces.classify`, the `memories/` and
`projects/<id>/memory/` layouts are `private_excluded` beneath every
caller-declared root, whatever its label, so `--external-root codex=~/.codex`
keeps them unread as `codex_home` does. Checkout folders with those names keep
their own verdicts. Every capture shares the harness inventory's
50000-fact bound, and each digest in a list fact counts toward it, so an
oversized approval list or key table is `blocked` with `fact_bound_exceeded`,
never truncated. A row's trust is `declared_trusted` only for
checkout rows under a `declared_trusted` project. An undeclared or
`declared_untrusted` project layer, and every declared root, stays `unknown`,
because Codex loads project config only for trusted projects.
`observed_loading` and `effective` are always `unknown`.

`server_sha256` and `telemetry_server_sha256('mcp__<server>__<tool>')` share
one digest function, so telemetry tool names can be compared with inventory
servers. Plugin and ambiguous names give `null`, and no match means `unknown`,
not absence. AHL's own derivation snapshot, `derivations/<id>/settings.json`,
classifies as `private_excluded` by its path. Content never hides a file at a
runtime settings path: a `settings.json` there is inventoried whatever keys it
holds. In the surface walk, `.mcp.json` and output styles are `config` nodes
whose parser reports path-free row counts. The settings files and
`.codex/config.toml` stay `hook` nodes with a `config` facet, and the hook
parser owns them there. Undecodable files become a `parse_failed` presence row.
JSON and TOML share one nesting bound of 64, so a deeply nested
`.codex/config.toml` is also `parse_failed`, never a crash. A CLI, the surface
walk's pruning of memory directories under declared roots, and profile-scoped
Codex keys remain open in #281.

### Surface-faithful trial workspaces

A cooperative Claude Code or Codex trial copies the fixture, then each
`harness_paths` file at its relative path, at any depth. A path that the version
lacks is removed from the copy, so the absence is faithful too. Each trial
records `workspace_surfaces`: the included paths with their `surfaces.classify`
kind and SHA-256, the absent paths, and `observed_loading: "unknown"`. A source
path that crosses a symlink is refused.

Each trial also asks the runtime to skip user, local and global layers. Every
request is gated on a `surfaces.CAPABILITIES` row, and a request without a row
is not made:

| Runtime | Request | Capability row |
| --- | --- | --- |
| Claude Code | `--setting-sources project` | `settings:claude-code` |
| Claude Code | `--strict-mcp-config --mcp-config` with `{"mcpServers": {}}` | `mcp:claude-code` |
| Claude Code | `--settings` with `{"disableAllHooks": true}` | `hooks:claude-code` |
| Codex | temporary `CODEX_HOME` | `settings:codex` |

The Claude files and the temporary `CODEX_HOME` are private and sit beside the
workspace, never inside it. The Codex home holds only one `config.toml`, with the
`model` and `model_reasoning_effort` copied from the user configuration and one
`trust_level = "trusted"` entry for the trial workspace. With that trust entry,
staged project `.codex` surfaces can load. Neither the argv nor the environment
of a trial names the real `CODEX_HOME` or `~/.claude`. Claude trials drop
`CODEX_HOME` from their environment.

The temporary Codex home holds no `auth.json`, so a saved `codex login` never
reaches a trial. At Codex 0.166.0, `codex login status` under such a home
reports `Not logged in` even with `CODEX_API_KEY` set, because it reads only
saved credentials. `CODEX_API_KEY` is the documented credential override for
`codex exec`; `OPENAI_API_KEY` is not documented for it. Before any trial,
`trial_surfaces.codex_authentication` records `runtime.codex_authentication`:

| Value | When | Effect |
| --- | --- | --- |
| `environment_variable_unobserved` | `CODEX_API_KEY` is set | Trials run; acceptance of the key is not observed |
| `unavailable` | No key, and `--version` prints the `codex-cli` banner | The run blocks as `codex_credentials_unavailable` |
| `unknown` | No key, and the binary is not recognised as the Codex CLI | Trials run; nothing is claimed |
| `inherited` | No temporary home is requested | Trials use the inherited home |

`--setting-sources project` also drops the permission rules, sandbox settings,
`env` and `apiKeyHelper` of the user and local settings files. The descriptor
lists them under `excluded_requested`. Only project and managed permission rules
are inherited, so put the rules a trusted fixture needs in its
`.claude/settings.json`. Authentication stored outside settings files is kept.

`results.json` records the `isolation` descriptor (`ahl-trial-surfaces-v1`) in
place of the former free-text admission. It lists each request with its row
status, plus the layers that are requested excluded, inherited (Claude
authentication stored outside settings files, project and managed permissions,
managed settings, environment, `HOME/.agents/skills`) or unknown.
All four rows are `unverified` at the pins in `surfaces.PINS`, so the status is
`inherited_unverified`. Rows marked verified give
`requested_documented_unobserved`. No descriptor says `isolated`, and
`security_isolation`, `network_isolation` and `full_certification` are always
false. Only fake-runtime tests run in CI. A native smoke run is reported
separately, as observed or skipped, and it is the only thing that can move a row
to verified.

The controlled Responses executor accepts an optional task `cwd`, a safe
relative directory. Its effective instructions are then the `AGENTS.md` chain
from the harness root down to `cwd`, in that order, one file per directory. Each
member is recorded as present or absent with its mode, SHA-256 and size.
`AGENTS.override.md` and fallback names are not modelled. A chain over Codex's
default 32 KiB `project_doc_max_bytes` blocks before any request and is never
truncated. Tasks without `cwd` keep the root-only `AGENTS.md` context unchanged.
`cwd` requires `instruction_protocol`; preflight checks that and each frozen
chain's size before credentials, inference or output. Other executors reject
`cwd` as a setup error. The closed project input profile still signs only
the root `AGENTS.md`.

Sources: [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference),
[Claude Code settings](https://code.claude.com/docs/en/settings),
[Codex configuration reference](https://developers.openai.com/codex/config-reference) and
[Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
retrieved 2026-11-05 for Claude Code 2.1.317 and Codex 0.166.0.

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
observed through successive imports; Git history and native session correlation
remain open in #43. Declared handoff/workstream links are described
[below](#declared-handoff-and-workstream-provenance). A separate explicit
[selected MCP page capture](memory-page-capture.md) now supports hash-only journals.

The reader imposes no concept, directory-entry, frontmatter, per-page or aggregate
byte ceilings. Links/special files, nested
scope manifests and project mismatches are refused. `.git`, `_pending`, reserved
index/meta/log files and monthly `log-YYYY-MM.md` names are excluded; a conforming
ordinary note using a monthly-ledger filename is also excluded in this first slice.
Coverage remains unknown. Use an extracted static export for stable input; this is
not an atomic snapshot of a concurrently edited wiki. Tests use synthetic bundles
and do not read real memories or call models.

### Declared handoff and workstream provenance

`sources.memory_provenance.bind_provenance(connector, source, store)` relates
declared handoff and workstream records to session and page records of one
authorized OKF bundle that was already imported into `store`. It is an offline
Python API with no CLI exposure. It re-reads the bundle through `OKFExport.pages`,
so the declaration, root, project and version checks above apply, and it reads the
selected journal. It writes nothing, runs no ai-memory command, opens no network
connection and never reads SQLite.

Neither the pinned OKF v0.2 document nor upstream `main` (checked at
`8e122ef82a826f5e39e11779b56e2d80119ee17a`) defines a handoff or workstream concept
type. Upstream keeps handoffs and managed workstreams in its server database. This
profile therefore reads a caller-declared concept shape, reported as
`shape: caller_declared_okf_concept` with `upstream_format_documented: false`.
The field names follow the public handoff tool documentation:

| Field | Meaning |
| --- | --- |
| `type` | `Handoff` or `Workstream` |
| `handoff_id` / `workstream_id` | Required record identifier, unique per kind in the bundle |
| `project`, optional `workspace` | Declared scope, compared with the source's project and scope fingerprints |
| `created_at` | Offset-bearing creation time |
| `from_session` | Handoffs only: the session that left it |
| `linked_sessions`, `linked_pages` | Lists of exact session identifiers and bundle-relative page paths |

A session identifier matches only a `type: Session Summary` page whose
`session_id` is the identical string. A page link matches only the identical path
of a concept in the bundle. No case folding, path normalization, URI parsing,
prefix search or filename inference is done. Links bind only when the bundle
still equals the journal's latest active checkpoint
(`okf.bundle_sha256(pages)`), so both endpoints are exact imported revisions.
Otherwise the report is `unknown` with `journal_unavailable` or
`bundle_not_latest_checkpoint` and has no receipts.

A bound link is a `JoinReceipt` with relation `handoff_from_session`,
`memory_links_session` or `memory_links_page`, namespace `ai-memory.handoff` or
`ai-memory.workstream`, and the record's parsed `created_at` as `effective_from`.
It stays `candidate` with basis `source_declaration`, because the export declares
the identifiers and nothing verifies them natively. Each failed link stays
`unlinked` with one of these reasons; a record has one unlinked receipt per
relation, and its rows list every failed target:

| Reason | Meaning |
| --- | --- |
| `target_missing` | No session page or bundle path matches exactly |
| `target_ambiguous` | Several session pages declare the identifier |
| `cross_scope` | The record names another project or workspace |
| `scope_undeclared` / `scope_invalid` | The record declares no project, or a non-text scope |
| `target_cross_scope` / `target_scope_undeclared` / `target_scope_invalid` | The target page declares another project or workspace, declares no project, or a non-text scope; targets need the same declared scope as the record |
| `field_not_applicable_to_kind` | A workstream declares `from_session`, which only handoffs use |
| `self_reference` | The record links to itself |
| `target_not_imported` | The target revision is not present in the journal |
| `link_field_invalid` / `from_session_missing` | A link field is malformed, or a handoff names no source session |

A record without a valid identifier is `invalid`. Records sharing one identifier
are `ambiguous_identity`. Both emit no links. Every other record also yields a
`TemporalFacet` whose `recorded_at` is `created_at`, for use with
[`resolve`](#declared-validity-as-of-an-instant). A naive, date-only, unparseable
or missing time stays unknown with its reason. Declared validity and handoff
lifecycle states (open, accepted, expired) are not interpreted.

Reports contain fingerprints, record references, receipts, counts and the declared
`created_at` times (as receipt `effective_from` and facet `recorded_at`). Bodies,
summaries, next steps, identifiers, paths and scope names are never included.
Fingerprints are unsalted SHA-256 values, so a holder of a private report can
confirm guessed low-entropy values such as project names or short identifiers
(tracked in #237). The handoff body is represented only by its page revision hash.
`counts.distinct_targets` counts linked (candidate) targets once, however many
records link them; unlinked targets are not counted. Reports carry profile `ai-memory-okf-provenance-v1`, `delivery_observed`
and `injection_observed` false, `native_session_correlation: not_attempted`,
`independent_roots: null`, `persisted: false` and `publication_allowed: false`,
and public benchmark export rejects them.

These receipts establish exact declared-identifier co-occurrence inside one
scoped bundle and journal. They do not establish that a handoff was delivered,
claimed or injected into an agent, that an ai-memory session is a Codex or Claude
native session, that its content is true, or that linked records are independent.
`task test:sources:memory-provenance` runs synthetic bundles only. Still open in
#43: native session correlation across journals without duplicate roots, the
upstream database or MCP handoff and workstream surfaces, delivery and injection
evidence, persisted links and lineage components, and deletion reconciliation.

### Declared validity as of an instant

`sources.temporal` is an offline Python API for the knowledge sources described in
#47: OKF pages, LLM wiki pages, Markdown knowledge-graph notes and ADRs. It does
not read pages. A caller supplies one `TemporalFacet` per exact imported revision
(`RecordRef`). The facet holds the knowledge time `recorded_at`, the author-declared
`valid_from` and `valid_until` (or an explicit `open_ended` declaration), a decision
status (`unspecified`, `proposed`, `accepted`, `rejected`, `deprecated` or
`superseded`) and explicit `supersedes`/`superseded_by` references.
`resolve(facets, at)` returns one row per record with a status (`in_force`,
`superseded`, `not_yet_valid`, `expired`, `not_adopted` or `unknown`) and the
reasons for it. `resolve_store` binds facets to present revisions of one validated
`SourceStore` journal, treats the journal's revision supersession edges as
successors and reports the journal's SHA-256. Neither function writes anything.
Existing record, journal and source fingerprints are unchanged.

The default `historical` view is a replay. It considers only records whose
`recorded_at` is at or before `at`. A record first recorded later is listed under
`not_known_at_instant`, and the supersessions it declares do not apply, even when
it claims an earlier `valid_from`. `knowledge='as_known_now'` is a separate
retrospective view that must be requested explicitly and applies every supplied
record. Its output carries `retrospective: true` and a different input fingerprint.

A status stays `unknown` when the facts it needs are missing:

- `at` must carry a UTC offset. A naive or unparseable query instant is rejected.
- Missing, unparseable or timezone-less declared times give reasons such as
  `valid_from_timezone_missing`. A date without a time has no offset and is unknown.
  An unknown `recorded_at` blocks a historical replay but not the retrospective view.
- Records on a supersession cycle, including a self-loop, are `unknown` with
  `supersession_cycle` and listed under `conflicts`. The cycle is never broken arbitrarily.
- Two revisions of one native record known at the instant without an ordering edge
  are both `unknown` with `ambiguous_revisions`. Only a path from one revision to
  another revision of the same record orders them; edges to other records do not. Two successors of one record that
  do not supersede each other are both `unknown` with `competing_successors`.
  Arrival order, `recorded_at` order and collection time never choose a winner.
  Only explicit declarations do.
- A predecessor is `unknown` when a successor is structurally unknown, has no facet
  (a deletion tombstone, for example), is not yet known at the instant, or has no
  usable `valid_from`. A proposed or rejected successor does not take effect.
  An effective successor supersedes its predecessor once its `valid_from` has passed,
  even when the successor's own end is unknown, and supersession is reported before
  the predecessor's own decision status (a superseded `proposed` record reads
  `superseded`, not `not_adopted`). Records and conflicts are emitted in a canonical
  order independent of input order.
- A self-declared `deprecated` or `superseded` status takes effect no later than the
  revision's `recorded_at`. When that time is unknown or after the instant, the status
  is unknown. A declared interval is half-open, so the `valid_until` instant is expired.

References to records without a facet stay visible under `dangling`. Each result
carries `profile: knowledge-temporal-v1`, `validity_source: author_declared`,
`truth_established: false`, `authority: none` and `publication_allowed: false`,
and `public_export_allowed` rejects that profile. An `in_force` row means only that
the author's declarations agree at that instant. It does not mean the statement is
true or reviewed, that an agent retrieved it, or that it helps with a task.
Synthetic fixtures establish this contract, not factual accuracy.

Still open in #47: the readers do not yet map OKF, wiki, knowledge-graph or ADR
fields to facets, resolve their links to `RecordRef`s, or store facets. Claim
identity across sources, project-scope compatibility checks, review provenance and
`stale_after` handling in the resolver, contradiction detection between statements,
a CLI and the #43/#58/#11 integrations are also still open.

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
incomplete content and duplicate JSON keys fail the whole collection. A non-200
status raises `HTTPStatusError`, a `TransportError` that keeps only the status
code, so a provider reader can map a denial to an explicit coverage state. There are
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

### Session-observation field contract

`ahl analyze --memory-manifest` reads `memory_read_session_observations`. Each row
follows the public upstream
[`ObservationRecord`](https://github.com/akitaonrails/ai-memory/blob/e6715cf08142183a7b2737cc909575a35ce209b0/crates/ai-memory-store/src/reader.rs#L1906)
(ai-memory 3.4.0). The pinned manifest fingerprints the tool's input schema, not
this output record, so the reader handles each row field explicitly:

| Upstream field | Normalized use |
| --- | --- |
| `id` | `event_id`; duplicates reject the capture |
| `session_id` | Must equal the selected session; becomes `case_id` |
| `kind` | `activity`: `pre-tool-use` → `tool_call`, `post-tool-use` → `tool_result`, `user-prompt` → `user_message`, `stop` → `turn_stop`; other kinds pass through unchanged |
| `created_at` | `timestamp` |
| `title`, `body` | Not normalized (see below) |
| `importance` | Not normalized; it is a capture heuristic, not an outcome |
| `extension`, `source_event` | Not normalized; an opt-in caller vocabulary, not tool identity or result |

The record has no field for tool name, call ID, exit code or result status. For some
agents and capture policies, upstream writes a text header into the tool `body`.
The header can hold `tool_family`, `tool_call_id`, `mcp_server`/`mcp_tool` and
`tool_name` lines, and on `post-tool-use` an `outcome: success|error|unknown` line,
followed by `\n---\n` and an output excerpt
([`TOOL_BODY_SEPARATOR`](https://github.com/akitaonrails/ai-memory/blob/e6715cf08142183a7b2737cc909575a35ce209b0/crates/ai-memory-core/src/observation.rs#L17)).
Other agents get free-form titles and bodies. These lines live in sanitized body
text, which the server truncates, and this reader requests 200-character bodies,
less than upstream's 512-character header bound. The outcome is what the harness
reported, not a verified result. Tool titles may also embed a tool name.

The reader never parses titles or bodies. Every event therefore keeps `outcome`
and `outcome_provenance` `unknown` with
`missing_reason=source_lacks_structured_tool_and_outcome_metadata`. Pre/post
events are never paired into calls, and `memory_source.missing_fields` lists
`tool_name`, `call_id`, `execution_status`, `verified_outcome` and
`independent_lineage`. Row fields outside this record, such as `tool_name`, `call_id`
or `status` from a different server build, are ignored and do not change events.
A structured mapping needs upstream record fields, or a versioned body-header
contract accepted for this project, and then a new pinned profile. Until then,
use a native capture for tool calls and results.
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

## Explicit Gitea REST captures

`sources.gitea.GiteaAPI` is a standard-library, GET-only reader for selected
issues and pull requests of one explicitly named repository on a self-hosted
Gitea instance. It does not assume GitHub API compatibility. Construct:

```python
GiteaSelection(base_url="https://gitea.example.test", owner="synthetic-owner",
    repo="synthetic-repository", capture=("comments", "issues", "pulls", "reviews"),
    issues=(7,), pulls=(8,))
```

`base_url` is the canonical HTTPS instance root: lowercase host, no default
`:443`, no trailing slash, query, credentials or `/api/v1` suffix; a sub-path
such as `https://gitea.example.test/forge` is allowed. `http://` and every other
form are refused before any connection. `capture` is an ascending subset of
`comments`, `issues`, `pulls` and `reviews`; `issues` and `pulls` must appear
exactly when numbers are selected, `reviews` requires `pulls`, and one number
cannot be both an issue and a PR (Gitea shares one index). There is no
repository listing, search or enumeration: only the named numbers are read.

Use `SourceSpec(kind="api", provider="gitea", version="v1", root_sha256=digest(base_url),
project_sha256=selection.project_sha256, scope_sha256=selection.sha256,
identity_provenance="source_declared_id", enabled=True)` and register
`Capability("api", "gitea", "v1", ("contextual_assertion", "development_artifact"))`.

Identity is the instance (host, port and root path) plus the case-insensitive
`owner/repo` name, then the issue/PR number or the comment/review ID. The same
number on another host, sub-path or repository has a different project and
native identity. Each PR side keeps its own repository identity: a fork head is
`head_repository_state: "other_repository"` with the fork's repository hash, even
when its branch name and commit match the base; a deleted head repository is
`unknown`. Issue repository, PR base repository and native comment/review parent
links are checked against the selection; a foreign parent, a plain issue
selected as a PR, or an issue/PR/repository that changes between its read and
its readback fails the whole capture.

Requests go to `/api/v1/version`, `/api/v1/repos/OWNER/REPO`, `.../issues/N`,
`.../issues/N/comments`, `.../pulls/N` and `.../pulls/N/reviews`. The server
version is recorded when `/api/v1/version` reports one, otherwise
`server_version: "unknown"` with the observed state (`not_found`, `forbidden`,
`unrecognized` and so on). Records are `gitea_instance`, `gitea_repository`,
`gitea_issue`, `gitea_pull`, `gitea_comment` and `gitea_review`. Titles, bodies,
labels, branch names and users are SHA-256 fingerprints, as in the GitHub reader;
raw text, owner and repository names are neither stored nor printed. PRs keep
base/head repository, branch and commit hashes, state, draft (or `unknown`),
`merged`, the merge commit and merge base. Reviews keep their state (`APPROVED`,
`COMMENT`, `PENDING`, `REQUEST_CHANGES`, `REQUEST_REVIEW`, otherwise `unknown`),
commit, whether that commit matches the captured head, and `stale`, `dismissed`
and `official` flags. A closed issue, merged PR or approval is a native claim
only: `task_outcomes_verified` is false and records keep causal status and
independence `unknown`.

Listings send `page` and `limit=50`. A `Link` `rel="next"` must name the same
collection with exactly the next page and the same limit, or the capture fails.
`X-Total-Count` must stay constant and match the rows received. A missing next
page, a changed or mismatched total, a full last page without a total, an issue
comment count that differs from its listing, and a budget spent mid-listing each
leave `pagination_incomplete` coverage with the reason in `basis`.

A server that ignores `page`/`limit` may answer with one longer first page of at
most 1,000 rows; a longer response, or any later page above the limit, fails the
capture. Budgets left unset in the configuration default to 1,000 requests,
256 MiB and 900 seconds; configured values take precedence. The byte budget is
cumulative; each response is also limited to 8 MiB, because it is held and
parsed whole. A response above that limit fails the capture before parsing.
A listing reduces each page to its digest nodes before it requests the next one,
so raw rows from earlier pages are never kept. A row that fails that reduction,
such as a non-string `body`, fails the capture on the page that carries it.

HTTP 401, 403, 404 and 429 become coverage states (`unauthorized`, `forbidden`,
`not_found`, `rate_limited`) on the resource that returned them. A 429 stops every
later request; there is no automatic retry. A spent page, byte or deadline budget
is `budget_exhausted`, whether it runs out before a request, while a response
body would pass the remaining bytes, or when the deadline (including a socket
timeout shortened to it) arrives during a request; it also stops every later
request. Any of these, and any incomplete listing, makes the
snapshot `checkpoint_ready: false`; the coverage manifest adds `not_observed` and
marks `limit_reached`/`truncated`, and the registry refuses to commit it. The one
exception is a 404 from the review listing of an already read PR, which older
servers return: it is `unsupported_endpoint` with gap `unsupported_version` and
warning `review_endpoint_unsupported`, and the rest of the capture may still be
checkpointed. Other statuses, malformed JSON and non-JSON content fail closed
without a snapshot.

Repeat imports use the shared journal: an unchanged capture imports nothing, and
an edited comment, moved PR head or changed review state adds a new native
revision. The reader does not reconcile absences, so a comment missing from a
later listing creates no tombstone.

### Gitea CLI configuration

The manifest has the GitHub configuration shape: `schema_version: 1`, `source`,
`endpoint` (equal to `selection.base_url`), `selection` (`GiteaSelection.to_dict()`)
and optional `limits` (`max_pages`, `max_bytes`, `timeout`, `seconds`). Probe and
status never read credentials or contact the server. For `auth_mode: "configured"`,
pass either `--authorization-file PRIVATE_FILE` or `--authorization-env NAME`.
Both hold the complete Authorization header value, for example `token ...`.
Only the named file or variable is read; there is no implicit lookup of Gitea
CLI settings, Git credentials, netrc files or other environment variables, and
`--authorization-env` is refused for GitHub configurations. A missing variable
exits 3. Preview prints `gitea_summary` and `checkpoint_ready`. An import whose
capture is denied, rate-limited or incomplete prints `status: "blocked"` with the
summary, commits nothing and exits 3.

`task test:sources:gitea` runs the synthetic controls against a fake HTTPS API
on `gitea.example.test`: issues, PRs, comments and reviews; page/limit pagination
and each incomplete-page case; repeat import and update; cross-host, sub-path and
cross-repository number collisions; fork and deleted head identity; missing
review and version endpoints; 401/403/404/429 coverage; non-HTTPS refusal; and
GET-only requests. Live Gitea compatibility is not established by these fixtures.
Still open in #170: inline review comments (`/pulls/N/reviews/ID/comments`),
PR commits and files, timelines, issue/PR enumeration by state or time window,
absence tombstones, a relationship graph and native recheck, and CI/job evidence.

## Explicit GitLab REST v4 captures

`sources.gitlab.GitLabAPI` is a standard-library, GET-only reader for selected
issues and merge requests of one explicitly named project on GitLab.com or a
self-managed GitLab instance. Construct:

```python
GitLabSelection(base_url="https://gitlab.example.test",
    project="synthetic-group/synthetic-subgroup/synthetic-project",
    capture=("approvals", "issues", "merge_requests", "notes"),
    issues=(7,), merge_requests=(7,))
```

`base_url` follows the Gitea rules: a canonical HTTPS instance root, with a
sub-path such as `https://gitlab.example.test/gitlab` allowed and no `/api/v4`
suffix. `project` is either a numeric project ID (an integer) or a literal
`namespace/.../project` path of 2 to 21 segments. Each segment uses letters,
digits, `_`, `-` and `.`, cannot start with `-` or `.`, cannot end with `.`,
`.git` or `.atom`, and cannot contain `..`. Percent-encodings, empty or dot
segments, backslashes and queries are refused, so an encoded slash or a `..`
cannot leave the selected project. The path is sent once, URL-encoded as the
`:id` of `/api/v4/projects/:id`; every later request uses the numeric ID that read
returned. `capture` is an ascending subset of `approvals`, `issues`,
`merge_requests` and `notes`. `issues` and `merge_requests` must appear exactly
when IIDs are selected, `approvals` requires merge requests, and a selection
with no IID at all (for example `capture=("notes",)`) is refused. Issues and merge
requests have separate IID sequences, so one IID may be selected as both. Nothing
is listed, searched or enumerated: there is no group, project, issue or merge
request listing.

Use `SourceSpec(kind="api", provider="gitlab", version="v4", root_sha256=digest(base_url),
project_sha256=selection.project_sha256, scope_sha256=selection.sha256,
identity_provenance="source_declared_id", enabled=True)` and register
`Capability("api", "gitlab", "v4", ("contextual_assertion", "development_artifact"))`.

`selection.project_sha256` is the declared scope: the instance plus the numeric ID
or case-insensitive path, known before any request. Records key on the instance
(host, port and root path), the numeric project ID returned by `/projects/:id`,
the object kind and the IID, or the note ID for notes. Selecting the same project
by path or by ID therefore yields the same native identities under different
scopes. The same IID in another project, on another host or under another
sub-path is a different record, and so are issue 7 and merge request 7 of one
project. Each issue and merge request keeps its global `id` as a separate
`id_sha256`; it never replaces the IID identity. A note must name its parent's
`noteable_type` and global `noteable_id` (and, when sent, its `noteable_iid` and
`project_id`). A merge request whose `target_project_id` is another project
fails the capture. Its `source_project_id` keeps its own project identity, and a
fork source is `source_project_state: "other_project"`. GitLab sets
`source_project_id` to null when the source fork is deleted; that merge request
is still recorded with `source_project_state: "unknown"` and no source project
hash, and the capture continues. A response that omits `source_project_id`
entirely is malformed and fails the capture. A project path that
differs from the selection, a project or object that changes between its read
and its readback, or a non-public project read without authorization fails the
whole capture.

Requests go to `/api/v4/version`, `/api/v4/projects/:id`,
`.../issues/IID`, `.../merge_requests/IID`, their `/notes` and
`.../merge_requests/IID/approvals`. The server version is recorded when
`/version` reports one. Otherwise it is `server_version: "unknown"` with the
observed state; GitLab usually answers `unauthorized` without a token. Records
are `gitlab_instance`, `gitlab_project`, `gitlab_issue`, `gitlab_merge_request`,
`gitlab_note`, `gitlab_system_note` and `gitlab_approvals`. Titles, descriptions,
note bodies, labels, branch names, project paths and users are SHA-256
fingerprints, and IIDs are hashed. Merge requests keep source and target project
and branch hashes, `sha`, `diff_refs.base_sha`, `merge_commit_sha`,
`squash_commit_sha`, state (`opened`, `closed`, `locked`, `merged`), `merged_at`
and `draft` (or `unknown`). These SHAs record what the server reported; they
create no ancestry (warning `merge_and_squash_shas_not_ancestry`). Issues keep
state and `confidential`. Notes with `system: true` become `gitlab_system_note`
records linked by `system_event_on`; user notes are `gitlab_note` linked by
`conversation_on`, with `internal` (or an older server's `confidential`), `resolvable` and `resolved` flags. The ordinary
Notes API is not guaranteed to return every system event, so the summary warns
`system_note_coverage_unverified`. Approvals keep `approved`, the required and
remaining counts and hashed approvers, plus `approvers_unidentified` for approvers
whose `user` is missing or has no `id`; such an approver is counted, not fatal,
while a `user` that is not an object still fails the capture. Global IDs are hashed with their resource type (issue, merge
request, milestone), because GitLab numbers each type separately; a boolean is
never accepted where a native integer is expected. A merged or closed merge request, a
closed issue, a resolved note or an approval is a native claim only:
`task_outcomes_verified` is false and records keep causal status and independence
`unknown`.

Note listings send `page`, `per_page=100`, `order_by=created_at` and `sort=asc`.
`X-Next-Page` must be empty or exactly the next page, `X-Page` and `X-Per-Page`
must match the request when sent, and a `Link` `rel="next"` must name the same
collection, query and next page and agree with `X-Next-Page`. The link may
repeat the route parameters `id` and `noteable_id`, but only with the selected
values. `X-Total` must stay constant and match the rows received. An empty
`X-Next-Page` marks the last page even when GitLab omits `X-Total` for large
listings. A changed or mismatched total, a full last page with neither a total
nor an empty `X-Next-Page`, a user-note count that differs from the parent's
`user_notes_count`, and a budget spent mid-listing each leave
`pagination_incomplete` coverage with the reason in `basis`. Keyset pagination
is not used. Response size and default budgets match the Gitea reader: one
unpaged first page of at most 1,000 rows, and 1,000 requests, 256 MiB and
900 seconds when unset.

HTTP 401, 403, 404 and 429 become `unauthorized`, `forbidden`, `not_found` and
`rate_limited` coverage on the resource that returned them, and a 429 stops every
later request. There is no automatic retry. Any blocked or incomplete coverage
makes the snapshot `checkpoint_ready: false` and the registry refuses to commit
it. A 404 from the approvals endpoint of an already read merge request is the one
exception: it is `unsupported_endpoint` with basis `approvals_not_found`, gap
`unsupported_version` and warning `approvals_endpoint_unsupported`. Other
statuses, malformed JSON and non-JSON content fail closed without a snapshot.
Repeat imports follow the Gitea semantics: an unchanged capture imports nothing,
and an edited note, moved head or changed approval adds a native revision. The
reader does not reconcile absences.

The CLI manifest has the Gitea shape with `selection` set to
`GitLabSelection.to_dict()`. For `auth_mode: "configured"`, pass
`--authorization-file PRIVATE_FILE` or `--authorization-env NAME` holding the
complete Authorization header value (for example `Bearer ...`). Only that file
or variable is read; there is no lookup of GitLab CLI settings, Git credentials,
netrc files or other variables. Preview prints `gitlab_summary` and
`checkpoint_ready`. A blocked import prints `status: "blocked"`, commits nothing
and exits 3.

`task test:sources:gitlab` runs the synthetic controls against a fake HTTPS API
on `gitlab.example.test`: issues, merge requests, user and system notes,
approvals and a missing approvals endpoint; offset pagination and each
incomplete-page case; unsafe or disagreeing pagination headers; IID versus
global ID; cross-project, cross-host and sub-path IID collisions; fork source
identity; nested path encoding and `..` or encoded-slash refusal; 401/403/404/429
coverage; non-HTTPS refusal; GET-only requests; and repeat import and update.
Live GitLab compatibility, including the exact route parameters GitLab repeats
in `Link` headers, is not established by these fixtures.
Still open in #54: threaded discussions and `DiscussionNote` resolution, MR
commits, changes and versions, state events, linked issues, pipelines, jobs and
test reports, confidential-visibility checks per note, absence tombstones and a
relationship graph.

The Gitea and GitLab readers share `sources.forge.CaptureLedger` (records,
coverage, offset listing, version and snapshot manifest) and the transport
helpers `guarded_get`, `metered_exchange`, `budget_spent` and `next_page`. The
GitHub reader uses `metered_exchange`, `budget_spent` and `next_page` for its
REST requests and pagination checks.

## Offline CI test result reports

Select `local_file/test-results/v1` in the existing local source declaration, with
`data` naming a selection descriptor next to the manifest:

```json
{"schema_version": 1, "commit_sha": "<full 40- or 64-hex SHA>",
 "reports": [{"path": "reports/junit.xml", "format": "junit-xml"},
             {"path": "reports/pytest.json", "format": "pytest-json"}],
 "limits": {"max_cases": 5000}}
```

The scope fingerprint must equal `digest(selection)`, so the declared commit, report
paths, formats and limits are all part of the scope. The commit SHA must be a full
lowercase 40- or 64-hex string; abbreviated, uppercase and padded values are
rejected. Report paths are relative, unique and contained in the manifest folder;
symlinks, hard links and special files are refused. `TestResultsExport` reads only
the selected files with no discovery, network, test execution, report-embedded
scripts or URL fetches. Each report is re-read after parsing, and the selection is
rechecked, so a report changed during capture rejects the whole capture.

Each test case becomes one `test_case_result` development-artifact record and each
report one `test_report` record. The record identity binds the declared commit, the
report path fingerprint, the case identity and an occurrence index. The case identity
is the report's own declaration: JUnit's nested `testsuite` names plus `classname`
and `name`, or pytest's `nodeid`. Journals and preview output keep only
fingerprints. Raw suite and case names stay in memory as `declared_cases` for
in-process callers. The same test at another commit or in another report is a
different record. A repeated identity within one report is kept as a further
occurrence with a `duplicate_case_identity` warning; neither occurrence is
dropped or merged.

`TestResult.status` is one of `passed`, `failed`, `error`, `skipped` or `unknown`,
and `reported_outcome` keeps the native label:

| Input | Status |
| --- | --- |
| JUnit case with no `failure`/`error`/`skipped` child | `passed` (`no_status_element`) |
| JUnit case with no status child but a status-like attribute (`status`, `result`, `outcome`, `skipped`, `failed`, ...) | `unknown` (`status_attribute`) |
| JUnit `failure`, `error` or `skipped` child | `failed`, `error` or `skipped` |
| JUnit case with two different status children | `unknown` (`conflicting`) |
| JUnit case with an unrecognized child such as `flakyFailure` or `rerunFailure` | `unknown` (`unrecognized`) |
| pytest `passed`, `failed`, `error`, `skipped` | the same status |
| pytest `passed` with a setup, call or teardown phase whose outcome is not `passed` | `unknown` (`conflicting`) |
| pytest `xfailed`, `xpassed`, `rerun` | `unknown` with that label |
| any other pytest outcome | `unknown` (`unrecognized`) |

Suite-level JUnit `failure`/`error` elements name no case; they are never
attributed to one and add `suite_level_status_unattributed`. `duration_seconds`
is JUnit's `time` attribute, or the sum of pytest's setup/call/teardown durations.
It is `null` when absent, and also when a value is malformed, negative or
non-finite, or when finite pytest phases sum to a non-finite total (with
`invalid_duration`). pytest collector failures add
`collection_failures_reported`; they create no case.

These results are CI evidence about the declared commit only. Every result carries
`commit_binding: caller_declared`, `commit_verified: false` and
`outcome_scope: declared_commit_ci_evidence`. Records keep causal status and
independence `unknown`. This slice does not establish that the commit is in any
journal, that the reports came from that commit's CI run, or that a rerun or
matrix entry is an independent trial (`reruns_not_independent`). A passing test is
not a verified task outcome of an agent run, and a failing one does not mean a
model caused it. The preview `test_results_summary` reports per-status counts,
warnings and `task_outcomes_verified`, `verification_authorized`,
`execution_authorized`, `scripts_executed`, `publication_allowed` and
`network_used`, all `false`. It carries profile `ci-test-results-v1`, which
`public_export_allowed` rejects.

Hostile-input handling uses only the standard library. Each report is read with a
byte bound before parsing. JUnit XML must be UTF-8 or US-ASCII with no NUL bytes
and no UTF-16/32 byte-order mark. Any `<!` markup declaration other than a comment
or CDATA section is refused before a parser is constructed: `DOCTYPE`, `ENTITY`,
`ELEMENT`, `ATTLIST` and conditional sections. Without a DTD there is no entity
expansion and no external entity; an undefined entity reference is a parse error.
Expat materializes a whole start tag and its attributes before reporting it, so
the same pre-parse screen also requires every start tag to be well delimited with at
most `max_attributes` quoted attributes (comments, CDATA and processing
instructions are skipped; an unterminated one is refused).
`xml.etree.ElementTree.XMLPullParser` then receives 64 KiB chunks and aborts on the
first bound exceeded: element count, depth, attributes per element and attribute
key/value length. Finished elements are emptied and detached as parsing proceeds,
so retained memory does not grow with element count. pytest JSON is screened
before decoding by the shared loader in `agent_harness_lab/sources/hardened.py`.
One streaming pass skips each string whole, so commas, colons and brackets
inside strings never count. Brackets are counted against `max_json_depth` and
`max_json_containers`, and scalar values (strings, numbers, `true`, `false` and
`null`, but not object keys) against `max_json_values`. The decoder builds the
whole tree first. Measured with `tracemalloc`, an empty container costs up to
about 150 bytes (roughly 48 times its input) and a scalar up to about 90 bytes.
Per input byte the worst scalar is a one-character string outside Latin-1, such
as `"Ā"`, at about 15 times its size; two-character ASCII strings reach about
11 times and `1.5` floats about 9 times. Both budgets can fill in one document,
so the bound is their sum: about 90 B × `max_json_values` plus 150 B ×
`max_json_containers`, under about 360 MiB at these ceilings. A 15 MB document
of `"Ā"` values and empty arrays that fills both budgets peaks at about 295 MiB.
Residual: object keys are not counted, because each key has a counted value, so
an object with many unique short keys passes every count. It peaks at about 20
to 24 times its size, bounded only by `max_report_bytes`: about 350 MiB for a
16 MiB report. The screen is a single linear pass whose string skip is
possessive, so it holds one match at a time even for one long string of escapes;
a string that never closes is refused there as `HostileReport` instead of being
rescanned from every later quote. The document is then decoded
strictly as UTF-8 with duplicate-key rejection and no `NaN`/`Infinity`, and the
`tests` schema is validated before any record is made. Each report's bytes are
released after parsing; the change check re-reads by streaming hash.
The case count is bounded across all reports. A refused report imports nothing and
leaves the previous checkpoint unchanged; `HostileReport` marks refusals from
these bounds.

| Limit | Ceiling |
| --- | --- |
| `max_reports` | 64 |
| `max_report_bytes` | 16 MiB |
| `max_cases` | 100,000 |
| `max_elements` | 500,000 |
| `max_depth` (XML) | 32 |
| `max_attributes` | 64 |
| `max_attribute_length` | 4,096 characters |
| `max_json_depth` | 64 |
| `max_json_containers` | 1,000,000 |
| `max_json_values` | 2,500,000 (100,000 tests of about 25 values) |

`limits` may only lower these ceilings. Limitation: Python's `xml.etree` and
`pyexpat` do not offer a supported switch that disables DTD processing. The
defense is therefore the byte screen run before parsing, plus the encoding
restriction that keeps the screen meaningful. Expat's own amplification limits
are not relied on. The screen is deliberately conservative: a comment or CDATA
section containing `<!` followed by anything other than `--` or `[CDATA[` is
refused too. Messages, stack traces, `system-out`/`system-err` text and report
properties are neither stored nor summarized. Element text still counts toward the
byte bound.

`task test:sources:test-results` runs the synthetic controls: status mapping,
billion-laughs and external-entity documents, hidden encodings, element/depth/
attribute/case/byte bounds with early abort, duplicate JSON keys, non-finite
numbers, deep nesting, duplicate case names, commit-identity validation and CLI
journals that hold hashes only. Still open in #53: GitHub Actions workflow-run/job/
attempt ingestion and job-step linkage; artifact download or zip handling; TAP,
SARIF, coverage and lint reports; joins from a result to a Git/GitHub commit or
PR-head record; correlated rerun replicates; expired-artifact coverage warnings;
and cross-source reporting.

## Offline Claude-Mem exports

Select `local_file/claude-mem-export/v1` in the existing local source declaration,
with `data` naming a selection descriptor next to the manifest:

```json
{"schema_version": 1, "claude_mem_version": "15.9.0", "instance": "laptop-a",
 "export": "exports/claude-mem.json", "limits": {"max_rows": 5000}}
```

The scope fingerprint must equal `digest(selection)`, so the declared release,
instance label, export path and limits are all part of the scope. The export is
one regular file contained in the manifest folder; symlinks, hard links and
special files are refused. `ClaudeMemExport` reads only that file. It never
starts or contacts the Claude-Mem worker, calls its MCP tools, opens its SQLite
database or Chroma store, or reads `~/.claude-mem`. The file is re-hashed after
parsing and the selection is rechecked, so a change during capture rejects the
whole capture.

### Pinned upstream shape

The adapter models one release:
[thedotmack/claude-mem `v15.9.0`](https://github.com/thedotmack/claude-mem/tree/v15.9.0),
commit `d0968e5d136abc34dcfe1b78acf3726fa84978d8`. Any other
`claude_mem_version` is refused. The shapes come from these files at that commit:

| Shape | Upstream file at the pin |
| --- | --- |
| Export envelope (`exportedAt`, `exportedAtEpoch`, `query`, optional `project`, four `total*` counts, `observations`, `sessions`, `summaries`, `prompts`) | [`scripts/export-memories.ts`](https://github.com/thedotmack/claude-mem/blob/d0968e5d136abc34dcfe1b78acf3726fa84978d8/scripts/export-memories.ts) |
| Observation, summary and prompt rows, plus search `rank`/`score` | [`src/services/sqlite/types.ts`](https://github.com/thedotmack/claude-mem/blob/d0968e5d136abc34dcfe1b78acf3726fa84978d8/src/services/sqlite/types.ts) |
| Session rows and the optional `agent_type`, `agent_id`, `platform_source`, `native_prompt_*` fields | `importSdkSession`, `importObservation`, `importSessionSummary` and `importUserPrompt` in [`src/services/sqlite/SessionStore.ts`](https://github.com/thedotmack/claude-mem/blob/d0968e5d136abc34dcfe1b78acf3726fa84978d8/src/services/sqlite/SessionStore.ts) |
| Migration columns the export's `SELECT o.*`/`s.*`/`up.*` queries also return (`content_hash`, `occurrence_count`, `title_norm_key`, `generated_by_model`, `relevance_count`, `reinforcement_dates`, `last_reinforced`, `merged_into_project`, `metadata`, and the `synced_at`/`origin_device_id`/`origin_local_id`/`sync_rev` sync columns) | `ALTER TABLE ... ADD COLUMN` migrations in the same `SessionStore.ts`; known but not modelled as claims |
| Required session identity per row kind | `importRowSchemas` in [`src/services/worker/http/routes/DataRoutes.ts`](https://github.com/thedotmack/claude-mem/blob/d0968e5d136abc34dcfe1b78acf3726fa84978d8/src/services/worker/http/routes/DataRoutes.ts) |
| Platform labels | [`src/shared/platform-source.ts`](https://github.com/thedotmack/claude-mem/blob/d0968e5d136abc34dcfe1b78acf3726fa84978d8/src/shared/platform-source.ts) |
| Export/import workflow | [`docs/public/usage/export-import.mdx`](https://github.com/thedotmack/claude-mem/blob/d0968e5d136abc34dcfe1b78acf3726fa84978d8/docs/public/usage/export-import.mdx) |

The shape is pinned only partly, and the profile says so: `upstream_shape:
source_inferred` and `compatibility: unverified`. Three gaps remain:

- `export-memories.ts` imports its `ExportData` type from `scripts/types/export.ts`,
  which is not in the tree at the pin. The envelope is therefore inferred from the
  object the script builds, not from a type definition.
- The export writes no version field, so the release is caller-declared
  (`version_attestation: caller_declared`, warning `export_version_unstated`).
  If an export does carry `version`, `claudeMemVersion`, `claude_mem_version`,
  `schemaVersion`, `schema_version` or `exportVersion`, a value other than the
  declared release is refused; a match records `export_declared`.
- The table listings in `docs/public/architecture/database.mdx` still use the
  older `sdk_session_id`/`claude_session_id` names. The adapter follows the code
  (`memory_session_id`/`content_session_id`), not that page.

No real export has been read; the tests use invented fixtures only.

### Capability matrix (v15.9.0)

| Upstream surface | This slice |
| --- | --- |
| `export-memories.ts` JSON file, selected explicitly | supported, read-only |
| Observations, summaries, prompts, sessions in that file | separate claim types |
| Search `query` and per-row `rank`/`score` | separate `search_hit` claims |
| `tool_uses` rows, advisor calls, pending queue | not in the export; not read |
| Worker HTTP API (`/api/search`, `/api/sdk-sessions/batch`) | not contacted |
| MCP `search`, `timeline`, `get_observations` | not called |
| SQLite `~/.claude-mem/claude-mem.db` (WAL) | not opened |
| Chroma vector projection | not read; never authoritative |
| Other Claude-Mem releases | refused |

### Claims

Each session row becomes one `MemoryClaim` and record; each observation, summary or
prompt row becomes two (its own claim plus a `search_hit` claim), and both count
toward `max_rows`. The claim types stay separate:

| Export array | Claim type (`record_type`) | Derivation | Record provenance |
| --- | --- | --- | --- |
| `observations` | `observation` (`claude_mem_observation`) | `observer_generated` | `model_hypothesis` |
| `summaries` | `generated_summary` (`claude_mem_generated_summary`) | `session_synthesis` | `model_hypothesis` |
| `prompts` | `user_prompt` (`claude_mem_user_prompt`) | `captured_prompt` | `native_field` |
| `sessions` | `memory_session` (`claude_mem_memory_session`) | `parent_session_lookup` | `native_field` |
| each observation, summary and prompt row | `search_hit` (`claude_mem_search_hit`) | `query_projection` | `native_field` |

The export file itself adds one `claude_mem_export` record whose revision is the
file's SHA-256. Claude-Mem builds observations and summaries with a model, so
their text is a model claim, not an observed event. A generated summary has
`synthesizes_multiple_origins: true`. It is never recorded as an observation and
is never a further independent case. The exporter selects rows with a search
query, so every selected row also gets a `search_hit` claim. The hit names its
target claim, its position in the export array, and whether a rank or score was
reported. A hit, or memory injected into context, is not evidence that an agent
used the memory. Every claim carries `independent_case`, `evidence_of_use`,
`task_outcome_verified` and `native_session_verified` set to `false`, and
`MemoryClaim` refuses any other value. A session `status` of `completed`, or
summary text such as "all tests pass", is not a task outcome.

Identity is hash-only and namespaced by the declared `instance` label. The native
ID is `digest(['claude-mem', instance, claim_type, id, occurrence])`; a search hit
also binds the query fingerprint. Projects, memory session IDs and content
session IDs are fingerprinted under the same namespace, so a prompt and its
session share a `content_session_sha256`, and observations and summaries share a
`memory_session_sha256`. These are Claude-Mem's own IDs. They are not verified
against any Claude Code or Codex transcript (`native_session_correlation:
not_attempted`). No correlation uses timestamps or content. Bodies (observation
`text`/`title`/`subtitle`/`facts`/`narrative`/`concepts`/`files_*`, summary
`request`/`investigated`/`learned`/`completed`/`next_steps`/`files_*`/`notes`,
`prompt_text`, session `user_prompt`/`custom_title`) are fingerprinted together
as `body_sha256` and never stored or printed. `platform_source`, observation
`type` and session `status` are kept only when they match the pinned label sets;
anything else becomes `other` or `unrecognized`. An edited body is a new revision
of the same native claim.

### Coverage and warnings

Nothing is dropped silently. The `claude_mem_summary` printed by `ahl sources
preview` reports per-type claim counts, plus these counts and warnings:

| Condition | Result |
| --- | --- |
| Row without a positive integer `id`, or without its session ID string | row skipped, counted in `rows_rejected`, `row_without_native_identity` |
| Row field outside the pinned field set | counted in `rows_with_unknown_fields`, `unknown_row_fields`; field names are not printed |
| Unrecognized session `status`, non-integer `prompt_number`, out-of-range timestamp offset | `unrecognized_session_status`, `invalid_prompt_number`, `invalid_timestamp` |
| Change only to an unmodelled field | the snapshot digest includes the export's SHA-256, so the checkpoint still moves |
| Extra top-level key | `unknown_envelope_fields` |
| Missing, naive or unparseable timestamp, or ISO and epoch values more than 1 s apart | `timestamps` count of `missing`, `invalid` or `conflicting`, matching warning, `original_at` null |
| Repeated `id` within one array | kept as a further `occurrence`, `duplicate_native_id` |
| `total*` count absent or not equal to its array length | `declared_total_missing` or `declared_total_mismatch` |
| No `project` filter in the export | `project_scope: all_projects`, `export_not_project_scoped` |
| Observation, summary or prompt whose `project` and `merged_into_project` both differ from the export filter, compared as the pinned search does (trimmed, ASCII case-insensitive, like SQLite `NOCASE`) | `row_outside_export_project`; session rows are not checked, because the exporter fetches them as parents by session ID rather than by project |
| Observation or summary whose memory session is not exported | `memory_session_not_exported` |
| One content session ID under two platforms | `content_session_shared_across_platforms` |

`summaries_not_independent`, `hits_not_evidence_of_use` and
`query_selected_projection` are always present. Capture stays `partial`: an export
is a query projection, not the whole store. The summary also reports
`task_outcomes_verified`, `evidence_of_use`, `publication_allowed`,
`network_used`, `worker_contacted`, `database_opened` and `bodies_stored`, all
`false`. It carries profile `claude-mem-export-v1`, which `public_export_allowed`
rejects.

Malformed input rejects the whole capture: non-UTF-8 bytes, a byte-order mark,
duplicate JSON keys, `NaN`/`Infinity`, a non-object envelope, a missing or
non-list array, or a non-object row. Decoding reuses the hardened `load_json`
in `agent_harness_lab/sources/hardened.py`, shared with the test-result source.
Before the decoder runs, one linear pass counts brackets against
`max_json_depth` and `max_json_containers`, and scalar values outside strings
(object keys excluded) against `max_json_values`; an unterminated string is
refused there. Refusals raise `HostileReport`. The same measured
costs apply: up to about 90 bytes a value and 150 bytes a container, so a
document that fills both budgets stays under about 490 MiB at these ceilings; a
23 MB export of `"Ā"` values and empty arrays peaks at about 395 MiB. Object
keys are not counted, so an export with many unique short keys peaks at about
20 to 24 times its size, bounded only by `max_export_bytes`: about 700 MiB for
a 32 MiB export. The export's bytes are released
before rows are mapped. A refused export imports nothing and leaves the previous
checkpoint unchanged.

| Limit | Ceiling |
| --- | --- |
| `max_export_bytes` | 32 MiB |
| `max_rows` (records admitted) | 100,000 |
| `max_fields` (per row) | 128 |
| `max_json_depth` | 16 |
| `max_json_containers` | 1,000,000 |
| `max_json_values` | 4,000,000 (100,000 rows of about 40 fields) |

`max_rows` counts the records an export admits, not its rows: each session row
counts once, each observation, summary and prompt row twice (its claim and its
`search_hit`), and the `claude_mem_export` record once. A rejected row still
counts, because the bound is checked before rows are mapped. At the ceiling an
export holds at most 49,999 hit-bearing rows, or 99,999 session rows.

`limits` may only lower these ceilings. `task test:sources:claude-mem` runs the
synthetic controls: each claim type, summary/observation separation, search-hit
targets, unknown and mismatched versions, duplicate IDs, unknown fields, missing
and conflicting timestamps, malformed JSON, bounds, path containment, capture
changes, repeat import, edits and CLI output with no body, ID or path. Still open
in #48: a capability-checked read of a configured worker or MCP server (tool
schema and version, bounded `search`/`timeline`, batched `get_observations`), an
authorized read-only SQLite snapshot mode with a verified schema, `tool_uses`
rows, multi-agent duplication checks against native Claude/Codex journals on
verified IDs, verifying the shape against a real export, and further releases.

## Offline Mem0 OSS memory and history exports

Select `local_file/mem0-oss-export/v1` in the existing local source declaration,
with `data` naming a selection descriptor next to the manifest:

```json
{"schema_version": 1, "mem0_version": "2.2.1", "instance": "laptop-a",
 "export": "exports/mem0.json", "unlisted_history": "exclude", "limits": {"max_rows": 5000}}
```

The scope fingerprint must equal `digest(selection)`. The export is one regular
file in the manifest folder, read and re-hashed through the same `LocalExport`
capture as the Claude-Mem source. `Mem0Export` never contacts a Mem0 server or
the hosted Platform, never opens `history.db` or a vector store, and never reads
`~/.mem0`. It calls no add, update, delete or reset operation.

Mem0 OSS has no export command, so the file is an AHL envelope around the
responses the pinned release returns:

```json
{"exported_at": "2031-06-01T00:00:00+00:00", "mem0_version": "2.2.1",
 "filters": {"user_id": "u1"}, "show_expired": true,
 "memories": {"results": [/* Memory.get_all / GET /memories items */]},
 "history": {"<memory id>": [/* Memory.history / GET /memories/{id}/history rows */]}}
```

`filters` is required. It names the `user_id`, `agent_id` or `run_id` values
the listing used, as the pinned `get_all` requires at least one. The unscoped
admin listing has no supported form. `exported_at` and `mem0_version` are
optional; `mem0_version` must match the declared release when present.

### Pinned upstream shape

The adapter models one release:
[mem0ai/mem0 `v2.2.1`](https://github.com/mem0ai/mem0/tree/v2.2.1), commit
`94c3fe9f238f3dbf29c9ce98643bd71eb13077cd`. Any other `mem0_version` is refused.

| Shape | Upstream file at the pin |
| --- | --- |
| `{"results": [...]}` items: `id`, `memory`, `hash`, `metadata`, `created_at`, `updated_at`, plus promoted `user_id`, `agent_id`, `run_id`, `actor_id`, `role`, `attributed_to`, `expiration_date` | `MemoryItem` in [`mem0/configs/base.py`](https://github.com/mem0ai/mem0/blob/94c3fe9f238f3dbf29c9ce98643bd71eb13077cd/mem0/configs/base.py); `get_all` in [`mem0/memory/main.py`](https://github.com/mem0ai/mem0/blob/94c3fe9f238f3dbf29c9ce98643bd71eb13077cd/mem0/memory/main.py) |
| History rows: `id`, `memory_id`, `old_memory`, `new_memory`, `event`, `created_at`, `updated_at`, `is_deleted`, `actor_id`, `role`, ordered by `created_at` then `updated_at` | `get_history` in [`mem0/memory/storage.py`](https://github.com/mem0ai/mem0/blob/94c3fe9f238f3dbf29c9ce98643bd71eb13077cd/mem0/memory/storage.py) |
| `ADD`, `UPDATE`, `DELETE` events; `DELETE` writes `is_deleted=1` and removes the vector entry | `_create_memory`, `_update_memory`, `_delete_memory` in `mem0/memory/main.py` |
| Expiry: `expiration_date` is an ISO date, hidden from `get_all` unless `show_expired` | `_payload_is_expired` in `mem0/memory/main.py` |
| REST routes `GET /memories`, `GET /memories/{id}/history` | [`server/main.py`](https://github.com/mem0ai/mem0/blob/94c3fe9f238f3dbf29c9ce98643bd71eb13077cd/server/main.py) |

The profile reports `upstream_shape: source_inferred` and `compatibility:
unverified`. The envelope is defined by AHL, no real export has been read, and
the tests use invented fixtures only. The hosted Platform (`/v3/memories/`
pagination and filters, `/v1/memories/{id}/history/`, the Platform export) is
`platform_v3: unsupported`. A `memories` object with keys beyond `results`, such
as Platform `count`/`next`, is refused rather than read with a guessed shape.

### Scope, lifecycle and claims

Every memory whose `user_id`, `agent_id` or `run_id` differs from a declared
filter is excluded with its history (`memory_outside_filter_scope`), so a wrong
listing cannot import another scope. The check runs before row validation: a
row from another scope that lacks memory text, or repeats a listed ID, still
excludes that ID and its history, and the first listing of a repeated ID is
dropped too. A row without a usable `id` is only counted as rejected, because no
history can be tied to it. History rows carry no scope of their own.
History for an ID absent from the listing (normally a deleted memory) is
excluded by default (`history_for_unlisted_memory_excluded`). With
`unlisted_history: include_unverified` it is imported with `scope_status:
unlisted_unverified` and warning `history_scope_unverified`.

Each listed memory becomes one `Mem0Claim` (`mem0_memory`, record provenance
`model_hypothesis`, since Mem0 may extract the fact with a model). Each history
row becomes one `mem0_history_event` claim (`native_field`) with its `event`,
`sequence` (export order) and fingerprints of `old_memory` and `new_memory`:

| Condition | Status | `currently_asserted` |
| --- | --- | --- |
| Listed memory, no expiry or expiry on or after `exported_at` | `asserted` | true |
| `expiration_date` before the date of `exported_at` | `expired` | false |
| Expiry present but unparseable, or no valid `exported_at` | `expiry_unverified` | false |
| Listed memory whose history ends in `DELETE` | `deleted` | false |
| Last `ADD` or `UPDATE` of an asserted memory whose `new_memory` matches the listed text and that is not flagged `is_deleted` | `current` | true |
| Event followed by a later event | `superseded` | false |
| `DELETE` event | `tombstone`; record `state: tombstone` | false |
| Any other last event: of an expired, deleted or unlisted memory, disagreeing with the listing, unrecognized or flagged `is_deleted` | `historical` | false |

Superseded, expired and deleted claims stay in evidence and are excluded only
from `currently_asserted`. An edit is a further version of one memory, never a
further independent success (`edits_not_independent`). `run_id` is a scoped
Mem0 identifier, not an AHL task root (`run_is_task_root: false`). A `score` is
retrieval relevance, recorded only as `score_reported`. Every claim carries
`independent_case`, `evidence_of_use` and `task_outcome_verified` as `false`, and
`Mem0Claim` refuses any other value, so a memory saying tests pass is not a
verified outcome.

Identity is hash-only: `digest(['mem0', instance, 'memory'|'history', id])`, and
user, agent, run and actor IDs are fingerprinted under the same `instance`
namespace. Memory text, metadata and history text are fingerprinted, never
stored or printed. An edited memory body is a new revision of the same native
memory.

### Coverage and warnings

The `mem0_summary` printed by `ahl sources preview` reports claim counts,
memories by status, `currently_asserted`, history events by type, tombstones,
`rows_rejected`, `rows_with_unknown_fields` and `excluded` counts.

| Condition | Result |
| --- | --- |
| Memory without a string `id` or `memory`, or a repeated `id` | rejected, `memory_without_identity_or_text` or `duplicate_memory_id` |
| History row without an `id`, with a repeated `id`, or whose `memory_id` differs from its key | rejected, `history_row_rejected` |
| Field outside the pinned sets | counted, `unknown_memory_fields` or `unknown_history_fields` |
| Event outside `ADD`/`UPDATE`/`DELETE` | `event: unrecognized`, `unrecognized_history_event` |
| `DELETE` without `is_deleted: true`, or the reverse | `tombstone_flag_mismatch` |
| First event not `ADD`; `old_memory` not the previous `new_memory`; decreasing change times | `history_starts_without_add`, `history_chain_break`, `history_out_of_order` |
| Last event disagrees with the listed memory, or is `DELETE` | `history_head_mismatch` |
| Listed memory with no history | `memory_without_history` |
| Missing or naive memory `created_at`, history change time or `exported_at` | `missing_timestamp`, `invalid_timestamp`, `export_time_unverified` |

`edits_not_independent`, `memories_are_contextual_claims` and
`run_id_not_task_root` are always present. Capture stays `partial`. The summary
reports `task_outcomes_verified`, `evidence_of_use`, `publication_allowed`,
`network_used`, `server_contacted`, `database_opened` and `bodies_stored` as
`false`, and profile `mem0-oss-export-v1` is rejected by `public_export_allowed`.

Decoding reuses the hardened `load_json`. Malformed JSON, missing `filters`, a
non-boolean `show_expired` or a non-list history value rejects the whole
capture and leaves the previous checkpoint unchanged. The ceilings are
`max_export_bytes` 32 MiB, `max_rows` 100,000 (memories plus history rows plus
the export record, checked before mapping), `max_fields` 64 per row,
`max_json_depth` 16, `max_json_containers` 1,000,000 and `max_json_values`
4,000,000. `limits` may only lower them. `task test:sources:mem0` runs the
synthetic controls.

Still open in #49: a read-only OSS REST client and the hosted Platform v3
profile with explicit origin, token source, pagination, retry and 401/403/404/429
stops, Platform fixtures with their own schema version, `app_id` scoping, the
hosted user-or-assistant attribution, categories and other Platform lifecycle
fields, and verifying the envelope against a real OSS capture.

No source is enabled by installation. Tests use synthetic metadata; they establish
contract validation, checkpoint behavior and a side-effect-free registry, not
real-source compatibility, containment, diagnostic usefulness or certification.
## Offline Hindsight bank archives

Select `local_file/hindsight-bank-archive/v1` in the existing local source
declaration, with `data` naming a selection descriptor next to the manifest:

```json
{"schema_version": 1, "hindsight_version": "1.1.0", "instance": "laptop-a",
 "bank_id": "team-notes", "archive": "exports/team-notes", "limits": {"max_rows": 5000}}
```

`archive` is a folder holding one transfer archive, already extracted from its ZIP.
The scope fingerprint is `digest(selection)`, so the declared release, instance,
bank and limits are all part of the scope. `HindsightArchive` reads
`manifest.json` first. If the manifest's `source_bank_id` differs from the
selected `bank_id`, the read stops with `PermissionError` before any document is
opened, and `ahl sources` exits 3 (blocked). A curation-archive row whose
`bank_id` names another bank also blocks the whole capture. No other bank is
discovered or read. The adapter then reads only the files the manifest names:
`documents/000000.json` through `document_count - 1`, `observations.json` when
`observation_count` is positive, and `data/invalidated_memory_units.json` when
`invalidated_memory_count` is positive. It never lists the folder. A named file
that is missing rejects the capture. Each file must be a contained, single-link
regular file. Every hash is rechecked after parsing, so a change during capture
rejects the capture.

The adapter never starts or contacts a Hindsight server, SDK, MCP tool or
database. It never calls `retain` (a write), `recall` (it can use inference),
`reflect` (a generated answer) or the asynchronous `transfer/export` operation.
The summary's `operations` field records these refusals.

### Pinned upstream shape

The adapter models one release:
[vectorize-io/hindsight `v1.1.0`](https://github.com/vectorize-io/hindsight/tree/v1.1.0),
commit `a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5`, HTTP API version `1.1.0`. Any
other `hindsight_version`, and any archive `schema_version` other than 1, is
refused.

| Shape | Upstream file at the pin |
| --- | --- |
| `TransferManifest`, `TransferDocument`, `TransferFact`, `TransferChunk`, `TransferCausalRelation`, `TransferObservation` | [`hindsight-api-slim/hindsight_api/engine/transfer/schema.py`](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-api-slim/hindsight_api/engine/transfer/schema.py) |
| Archive entries (`manifest.json`, `documents/NNNNNN.json`, `observations.json`, `data/invalidated_memory_units.json`) | [`hindsight-api-slim/hindsight_api/engine/transfer/export.py`](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-api-slim/hindsight_api/engine/transfer/export.py) |
| Curation archive columns (`invalidated_at`, `invalidation_reason`, `entity_ids`, `edited_at`) | [`alembic/versions/c9a1b2d3e4f5_add_invalidated_memory_units.py`](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-api-slim/hindsight_api/alembic/versions/c9a1b2d3e4f5_add_invalidated_memory_units.py) |
| `transfer/export`, `memories/list`, `recall` and `reflect` endpoints | [`hindsight-docs/static/openapi.json`](https://github.com/vectorize-io/hindsight/blob/a5a619b5ca59c5c39e0f96be6f7a31fabd136bd5/hindsight-docs/static/openapi.json) |

The profile says the shape is pinned only partly: `upstream_shape:
source_inferred`, `compatibility: unverified`, `version_attestation:
caller_declared`, and the warning `archive_version_unstated`. An archive carries
its layout `schema_version` but not the Hindsight release that wrote it. The
curation-archive rows mirror the `memory_units` table column for column, and only
the columns listed above, plus `id`, `bank_id`, `document_id`, `text`,
`fact_type`, `context`, the four date columns and `created_at`/`updated_at`, are
modelled. Any other column counts in `rows_with_unknown_fields`. No real archive
has been read; the tests use invented fixtures only.

### Claims and temporal status

| Archive part | Claim type (`record_type`) | Derivation | Record provenance |
| --- | --- | --- | --- |
| document | `retained_document` (`hindsight_retained_document`) | `retained_input` | `native_field` |
| document `facts[]` | `extracted_fact` (`hindsight_extracted_fact`) | `retain_extraction` | `model_hypothesis` |
| fact `causal_relations[]` | `fact_relation` (`hindsight_fact_relation`), naming its target fact | `retain_extraction` | `model_hypothesis` |
| `observations.json` | `consolidated_observation` (`hindsight_consolidated_observation`) | `consolidation_synthesis` | `model_hypothesis` |
| `data/invalidated_memory_units.json` | `invalidated_fact` (`hindsight_invalidated_fact`) | `curation_archive` | `model_hypothesis` |

One further `hindsight_archive` record carries the digest of every file's SHA-256
as its revision. A consolidated observation has `synthesizes_multiple_origins:
true` and a `source_count` of the facts it resolves to within the archive; it is
never a further independent case. Retrieved hits and reflections are not in an
archive and are not produced, so no recall or relevance score exists to map onto
a grader result, skill activation or session completion
(`retrieval_scores_used: false`). Every claim carries `independent_case`,
`evidence_of_use`, `task_outcome_verified`, `agent_defect_verified` and
`native_session_verified` set to `false`, and `BankClaim` refuses any other value.

Times stay in separate fields: `recorded_at` (the row's `created_at`, when the
memory was recorded), `mentioned_at`, `valid_from`/`valid_until` (the fact's
`occurred_start`/`occurred_end`) and, for curated facts, `invalidated_at`. A
naive or unparseable time is `invalid`. Upstream stores an open bound as year 1
or 9999, so those dates are `placeholder`, not instants. The `assertion_status`
of a fact or observation is judged at the manifest's `exported_at`:

| Condition | `assertion_status` |
| --- | --- |
| Row in the curation archive (a user invalidated it) | `invalidated` |
| `valid_until` at or before `exported_at` | `historical` |
| `valid_from` after `exported_at` | `not_yet_valid` |
| Live row with no ended or future window | `current` |
| `exported_at` missing or naive, an invalid validity time, or `valid_until` before `valid_from` | `undetermined` (`export_time_unverified`, `invalid_timestamp` or `inverted_validity`) |
| Document or relation | `not_applicable` |

A correction is therefore a separate claim: the retired fact becomes an
`invalidated_fact` with its own native ID, and any replacement fact is a new
`extracted_fact`. Historical and invalidated assertions have `review_candidate:
true`. That marks them for review. It is not evidence that an agent did anything
wrong.

Identity is hash-only and namespaced by `instance` and `bank_id`. A fact uses its
`source_id`, or its document and position when the ID is absent
(`fact_without_source_id`); an observation without one uses its archive position
(`observation_without_source_id`). Bodies, chunks, `retain_params`, tags, metadata,
entity names and invalidation reasons are fingerprinted as `body_sha256` and
never stored or printed. Bank names, tags and metadata are never matched against
Claude Code or Codex sessions (`native_session_correlation: not_attempted`).

### Coverage, bounds and warnings

The `hindsight_summary` printed by `ahl sources preview` reports claim and
assertion counts, rejected rows, rows with unknown fields, per-time status counts
and warnings. `facts_are_model_extractions`, `observations_not_independent`,
`invalidations_are_review_candidates` and `archive_version_unstated` are always
present. Other warnings: `duplicate_native_id` (kept as a further occurrence),
`row_without_native_identity`, `relation_target_unresolved` (the target index is
not an earlier fact in the same document), `unrecognized_relation_type`,
`unrecognized_fact_type`, `observation_source_unresolved` (a source naming no
captured fact; a repeated source is not unresolved),
`invalidated_fact_still_live`, `declared_total_mismatch`,
`unknown_manifest_fields`, `unknown_row_fields`, and `history_not_read` or
`<part>_not_read` when the manifest counts mental models, knowledge pages,
directives, webhooks, attachments or operations, none of which this slice reads.
Capture stays `partial`. The profile `hindsight-bank-archive-v1` is rejected by
`public_export_allowed`.

Decoding reuses the hardened `load_json` from `sources/hardened.py`, one file at a
time. Malformed JSON, duplicate keys, a non-object document or row, or a
non-list row file rejects the whole capture and leaves the previous checkpoint
unchanged. Every decoded file is held until mapping, so the JSON container and
value caps are one budget for the whole archive, and records are counted as each
file is decoded: a capture stops at the file that crosses a bound, before any
later file is decoded.

| Limit | Ceiling |
| --- | --- |
| `max_file_bytes` | 16 MiB |
| `max_archive_bytes` (all files) | 32 MiB |
| `max_documents` | 10,000 |
| `max_rows` (records admitted, counted per decoded file) | 100,000 |
| `max_fields` (per row) | 64 |
| `max_json_depth` (per file) | 16 |
| `max_json_containers`, `max_json_values` (all files) | 1,000,000, 4,000,000 |

`limits` may only lower these ceilings. `task test:sources:hindsight` runs the
synthetic controls. Still open in #51: reading the ZIP archive directly, an
optional bank-specific read mode over a verified read-only API with an explicit
URL and least-privilege token, mental models and knowledge pages as synthesized
claims, verifying the shape against a real archive, and further releases.

No source is enabled by installation. Tests use synthetic metadata; they establish
contract validation, checkpoint behavior and a side-effect-free registry, not
real-source compatibility, containment, diagnostic usefulness or certification.

## Offline Cognee graph exports

Select `local_file/cognee-export/v1` in the existing local source declaration,
with `data` naming a selection descriptor next to the manifest:

```json
{"schema_version": 1, "cognee_version": "1.6.5", "instance": "laptop-a",
 "dataset": "dataset-a", "dataset_id": "0f0e0d0c-0b0a-4908-8706-050403020100",
 "format": "graph_snapshot", "export": "exports/dataset-a.json",
 "limits": {"max_rows": 5000}}
```

The scope fingerprint must equal `digest(selection)`. The export is one regular
file contained in the manifest folder; symlinks, hard links and special files
are refused, and a change during capture rejects the whole capture.
`CogneeExport` reads only that file. It never imports Cognee, and it never calls
`recall`, `search`, `remember`, `cognify`, `improve` or `forget`. It opens no
relational, graph or vector store, runs no embedding or model, and contacts no
service. Producing the export is the user's own explicit step in Cognee. On-demand
retrieval is not implemented, and the summary reports `recall_used`,
`inference_used`, `graph_mutated`, `network_used` and `database_opened` as
`false`.

### Pinned upstream shape

The adapter models one release:
[topoteretes/cognee `v1.6.5`](https://github.com/topoteretes/cognee/tree/v1.6.5),
commit `37f788bd287750e10d7610173e5332b3843f6ebd`. Any other `cognee_version` is
refused. `cognee.export(dataset, format=...)` in
[`cognee/api/v1/export/export.py`](https://github.com/topoteretes/cognee/blob/37f788bd287750e10d7610173e5332b3843f6ebd/cognee/api/v1/export/export.py)
checks read permission on one dataset and writes that dataset's graph. Two of
its file shapes are accepted:

| `format` | Upstream writer at the pin | Shape |
| --- | --- | --- |
| `graph_snapshot` | `format="pydantic"` with a `destination`: `GraphSnapshot.save()` in [`cognee/modules/migration/snapshot.py`](https://github.com/topoteretes/cognee/blob/37f788bd287750e10d7610173e5332b3843f6ebd/cognee/modules/migration/snapshot.py) | `dataset_name`, `dataset_id`, `nodes`, and `edges` of `source_id`/`target_id`/`relationship`/`properties` |
| `nodes_edges` | `format="json"`: `write_json` in [`cognee/modules/migration/formats.py`](https://github.com/topoteretes/cognee/blob/37f788bd287750e10d7610173e5332b3843f6ebd/cognee/modules/migration/formats.py) | `nodes`, and `edges` of `source`/`target`/`relationship_name` with properties flattened beside them |

Node types come from the `DataPoint` subclasses at the pin:
[`DataPoint`](https://github.com/topoteretes/cognee/blob/37f788bd287750e10d7610173e5332b3843f6ebd/cognee/infrastructure/engine/models/DataPoint.py),
[`Document`](https://github.com/topoteretes/cognee/blob/37f788bd287750e10d7610173e5332b3843f6ebd/cognee/modules/data/processing/document_types/Document.py)
and its subclasses,
[`DocumentChunk`](https://github.com/topoteretes/cognee/blob/37f788bd287750e10d7610173e5332b3843f6ebd/cognee/modules/chunking/models/DocumentChunk.py)
(`document_id` and the `is_part_of` and `contains` fields), and
[`Entity`/`EntityType`](https://github.com/topoteretes/cognee/blob/37f788bd287750e10d7610173e5332b3843f6ebd/cognee/modules/engine/models/Entity.py)
(`is_a`). The `cogx`, `graphml` and `cypher` formats, an in-memory
`GraphSnapshot`, and any other `format` value are refused. A `graph_snapshot`
selection whose file has no `dataset_id` key is refused, and so is a
`nodes_edges` selection whose file has one. The shape is read from code, not
from a published schema, and no real export has been read: the profile reports
`upstream_shape: source_inferred` and `compatibility: unverified`. Neither
writer emits a version, so the release is caller-declared
(`version_attestation: caller_declared`). If an export does carry `version`,
`cognee_version` or `schema_version`, a value other than the declared release
is refused.

### Dataset scope

Every claim ID and the `dataset_sha256` are namespaced by `instance`, the
declared `dataset` label and the declared `dataset_id`. Cognee derives entity IDs
from names, so the same node ID in datasets A and B still yields two unrelated
claims, even when both datasets carry the same label, such as Cognee's default
`main_dataset`. `dataset_id` is a canonical lowercase UUID or `null`:

| Export states | Selection declares | Result |
| --- | --- | --- |
| the same `dataset_id` | that `dataset_id` | `dataset_lineage: export_matches_declaration`, `cross_source_joins: dataset_id_bound` |
| another `dataset_id`, or one the selection leaves `null` | anything | refused; nothing is imported |
| no ID (`nodes_edges`, or an empty `dataset_id`) | a `dataset_id` | `caller_declared`, joins `disabled`, `dataset_lineage_unverified` |
| no ID | `null` | `unknown`, joins `disabled`, `dataset_lineage_unverified` |

So dataset B's export cannot be imported under dataset A's selection, and a
file without a dataset identity never supports strong cross-source joins.

### Claims

Each node and edge becomes one `GraphClaim` and one record. The export file adds
one `cognee_export` record whose revision is the file's SHA-256.

| Graph item | Claim type (`record_type`) | Derivation | Record provenance |
| --- | --- | --- | --- |
| `Document` and its subclasses | `document` (`cognee_document`) | `ingested_source` | `native_field` |
| `DocumentChunk` | `chunk` (`cognee_chunk`) | `document_segment` | `native_field` |
| `Entity`, `EntityType` | `entity` (`cognee_entity`) | `model_extraction` | `model_hypothesis` |
| any other node type | `graph_node` (`cognee_graph_node`) | `unmodelled_datapoint` | `unknown` |
| `DocumentChunk` `is_part_of` a document, `DocumentChunk` `contains` an `Entity` or `Timestamp`, `Entity` `is_a` an `EntityType` | `relation` (`cognee_relation`) | `graph_edge` | `native_field` |
| any other edge, including a structural name between other or unexported node types | `relation` (`cognee_relation`) | `graph_edge` | `model_hypothesis` |

A structural edge is recognized by its name and both endpoint types, which come
from the `DocumentChunk.is_part_of`, `DocumentChunk.contains` and `Entity.is_a`
fields at the pin. An extracted relation between two entities that happens to be
called `contains` is a model hypothesis.

Edges and entities are derived evidence. Each claim keeps an `origin_status` and,
when it can be traced, the hashed ID of its origin document. That hash is the
document claim's `native_id_sha256`, never that of another node type. Only a
chunk's `document_id` and structural `is_part_of` and `contains` edges between
their pinned types trace an origin; a misnamed edge never does. A chunk's
`is_part_of` edge to a node absent from the export still names a candidate
document, exactly as a `document_id` does, so it can make a chunk `not_exported`
or `conflicting` but never `traced`. A relation's
`source_node_sha256` and `target_node_sha256` are the `native_id_sha256` of its
endpoint node claims. A repeated node ID resolves to its first occurrence:

| `origin_status` | Meaning |
| --- | --- |
| `self` | a document |
| `traced` | a chunk whose `document_id` and `is_part_of` edges name one exported document; an entity that only chunks of one such document `contain`; an edge from a document, or from a traced chunk or entity |
| `not_exported` | the named document is not in the export |
| `missing` | a chunk that names no document or only an exported node that is not a document, or an edge with an endpoint not in the export |
| `conflicting` | a chunk whose `document_id` and `is_part_of` edges disagree |
| `multiple` | an entity contained by chunks of more than one document, or by a traced chunk and an untraced one |
| `untraced` | no origin path in the export: an `EntityType`, an unmodelled node, or an edge from one |

A repeated relation is never a further independent case. Every claim carries
`independent_case`, `factually_verified` and `task_outcome_verified` set to
`false`, and `GraphClaim` refuses any other value. Node and relationship names,
`text`, `description`, `raw_data_location`, `external_metadata`, `edge_text` and
every other property are fingerprinted as `body_sha256` and never stored or
printed. A relation's name is kept only for a structural edge between its pinned types,
and is otherwise `other` plus a scoped `relationship_sha256`. A node's
`created_at` (epoch milliseconds) and `version` are kept. An edited property or
a bumped `version` is a new revision of the same native claim. A changed
endpoint is a different relation.

### Coverage and warnings

The `cognee_summary` printed by `ahl sources preview` reports claim counts,
`rows_rejected`, `origins` and `timestamps` counts, and these warnings:

| Condition | Result |
| --- | --- |
| Node without a non-empty string `id` | skipped, counted, `node_without_native_identity` |
| Edge without source, target or relationship strings | skipped, counted, `edge_without_endpoints` |
| Edge endpoint not among exported nodes | origin `missing`, `edge_endpoint_not_exported` |
| Chunk or entity origin other than `traced` | `chunk_origin_<status>` or `entity_origin_<status>` |
| Node `type` absent or outside the pinned types | `node_type_missing` or `unrecognized_node_type` |
| `version` not a positive integer | `invalid_node_version` |
| `created_at` missing, not an integer or out of range | `missing_timestamp` or `invalid_timestamp` |
| Repeated node ID or (source, relationship, target) | further `occurrence`, `duplicate_native_id` |
| Extra top-level key | `unknown_envelope_fields` |
| Snapshot edge key outside the four `GraphEdge` fields | `unknown_edge_fields` |
| Empty snapshot `dataset_name` | `dataset_name_missing` |

`entities_model_extracted`, `edges_not_independent`, `sessions_not_in_export`
and `retrieval_scores_not_in_export` are always present. Neither writer exports
sessions, ingestion pipeline runs or `recall` scores, so none is inferred.
Capture stays `partial`, because a graph export leaves out the relational `Data`
rows and the vector store. The profile is `cognee-export-v1`, which
`public_export_allowed` rejects.

Malformed input rejects the whole capture: non-UTF-8 bytes, duplicate keys,
`NaN`, a non-object envelope, missing `nodes`/`edges` lists, or a non-object node
or edge. Decoding reuses the hardened `load_json`, so the depth, container and
value screens and their measured costs match the Claude-Mem section above. The
limits and ceilings are the same: `max_export_bytes` 32 MiB, `max_rows` 100,000
(nodes plus edges plus the export record, checked before mapping), `max_fields`
128 per node or per edge, counting a snapshot edge's nested properties, `max_json_depth` 16,
`max_json_containers` 1,000,000 and `max_json_values` 4,000,000. `limits` may
only lower them.

`task test:sources:cognee` runs the synthetic controls: claim types and
provenance, origin tracing, dataset A/B separation, unknown lineage, missing
references, conflicting chunk documents, edge and version revisions, refused
releases and formats, bounds, capture changes, and CLI output and cache with no
body, ID, name or path while Cognee is unimportable. Still open in #52: verifying
the shape against a real export; `cogx` archives; sessions and session context;
code-symbol references; `recall` result import and an opt-in, explicitly
authorized, resource-marked retrieval mode; verified read-only API semantics;
bounded cancellation of result streams; further releases and backends.
