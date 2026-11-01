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

API transports, native provider formats, append offsets, analyze/doctor source
integration and remote pagination remain open in #42 and provider issues.
No source is enabled by installation. Tests use synthetic metadata; they establish
contract validation, checkpoint behavior and a side-effect-free registry, not
real-source compatibility, containment, diagnostic usefulness or certification.
