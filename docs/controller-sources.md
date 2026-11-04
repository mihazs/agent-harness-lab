# Live controller source observations

The paired evaluator can optionally ingest its actual offline worker and fresh
artifact-grader observations through the shared source registry and private
`SourceStore`. This is a bounded positive execution-relationship path for #42/#58,
not native Codex/Claude authentication or an independent task-outcome oracle.
No source is enabled by installation or default discovery.

The evaluator's existing offline/Responses command accepts three optional flags
together: `--source-cache`, `--source-project-sha256`, `--source-scope-sha256`.
They require the existing explicit cgroup, pinned rootfs and private disk profile.
The journal must be outside worker inputs, runtime images and run output. Project
and source-scope fingerprints are explicit declarations, not identities inferred
from directory names. Python callers can instead pass an exact
`sources.controller.SourceSink` as `args.source_sink` to `evaluate(args)`.

## Actual producer and consumer

Each selected trial opens a controller-owned attempt context. The real
`SandboxBackend` reserves a pending observation before preparation and binds it
to the actual subprocess, verified private workspace and delegated cgroup after
startup. Completion is observed after the worker tree is stopped, image/input
checks and bounded artifact extraction finish. An invocation that raises or loses
its completion cannot disappear from capture coverage.

The direct verified private-workspace subprocess qualifies without a monitor.
With `permission-results-v1`, the trusted observation privately binds the original
launch frame, exact actual monitor subprocess and pipe descriptors. Only bytes
sent/read through that owned pipe, actual EOF and the monitor's terminal state can
issue its one-use, attempt-specific completion. The existing Trace must have
observed every expected setup-to-role exec and reaped its owned tree. Public
`Observation.data`, parsed reports and permission-audit JSON cannot issue this
capability. Missing custody or incomplete execution keeps source identity unknown.

Both requested and effective argv are bound. The shared existing canonical Python
`-B` transform must match exactly, and the fresh grader must retain its fixed
requested command. This does not prove loaded executable identity. Completed
permission violations may substantiate the same limited execution facts while
remaining violations; source identity verification never grants permission or
isolation qualification. Terminal setup gaps remain incomplete setup coverage.

Artifact fingerprints come from bytes read through the actual private workspace
descriptor, or the controller's final in-memory working-state snapshot. They do
not come from a claimed output path after handoff. The fresh artifact-grader
producer matches those selected bytes against its actual extracted fixture and
frozen oracle inputs, fixed grader command and typed outcome checks. A swapped
artifact, missing worker result, mismatched scope, expired deadline or failed final
input check cannot produce positive source evidence. A false artifact outcome
does not itself invalidate observed runtime/grader identity.

This pairing compares selected relative paths and byte contents under the existing
`regrade_artifacts` materialization contract. It does not establish equivalent
filesystem modes, unselected files or transient effects; those require their own
frozen oracles and observations.

Closing freezes the exact attempt state in a private in-process issuance registry.
Copied objects, mutated captures, ordinary dictionaries, serialized attestations
and fake backends cannot recreate the capability. The trusted boundary is the
controller process and its real kernel/grader producers; this does not isolate
against arbitrary malicious Python executing inside that trusted host process.
Each attempt permits at most 128 worker observations and a 256 KiB receipt body.
Exhaustion keeps source verification unknown without retrying external work.
Coverage's `records` dimension counts kernel worker observations, not projected
source records or independent tasks. Its `bytes` dimension counts the encoded
receipt body, not the complete Python object graph or journal. Its `files`
dimension counts selected artifact paths, bounded by the materializer's existing
100-path limit, not all workspace files. Actual observed counts accompany each
limit; these remain declared, partial coverage summaries on journal replay.

The selected sink calls actual `Registry.preview`, `Registry.ingest` and
`Registry.recheck`. It uses the explicit `controller/ahl-controller/v1` capability,
with behavioral executor and grader-reference records. Live recheck requires the
same closed capability, active controller session and exact stored revisions.
Session exit explicitly retires its shared issuer on both normal and exceptional
paths. A saved Python context cannot begin, finish or read source authority after
that retirement; copying ContextVar values does not reopen the session. Issuance
is bound to the controller process ID; a forked process inherits no live authority.
The grader-to-executor `same_execution` link is verified only for that observed
artifact handoff. It does not prove causal origin or independent task roots.

## Field meanings and replay limits

| Field | Live observation |
| --- | --- |
| `session` | Fresh controller-session fingerprint; not a native agent session |
| `attempt` | Fresh controller-attempt fingerprint; not user-supplied task lineage |
| `runtime` | Declared post-run executable-path inventory fingerprint; not the loaded executable |
| `runtime_version` | Worker rootfs pin matched by actual before/after checks, not a served model revision |
| `grader_revision` | Exact frozen bundle executed by the fresh artifact grader |
| `project` | Declared project fingerprint |

Session and attempt fingerprints hash freshly minted nonces under distinct versioned
namespaces. The executable-path inventory can change after launch; it is never
promoted to loaded-binary verification. No claim about the actual loaded executable
follows from hashing the selected pathname after execution.

Effective harness/configuration, memory injection, served model, Git/code revision,
native identities, task-root independence and oracle validity remain unknown.
Mounting a harness does not establish that its instructions were consumed. Existing
artifact grading decides outcomes separately; source records confer no new grade,
adoption, permission or statistical independence authority.

The immediate live recheck also exposes `controller_harness_input`: the selected
executor's controller-observed `harness` input-tree digest, checked before and after
execution. Its `status` is `observed` when that input was captured, or `unknown`
with `sha256: null` when absent. The digest comes only from the same sealed owned
attempt as the executor/grader link. It describes the controller's input checks,
not an atomic snapshot, worker instruction consumption or effective configuration;
`effective_loading` remains `unverified`. No raw paths or input contents are added.
This private projection does not add a verified identity field or a journal schema.

Every journal entry retains **declared fields and candidate joins**. Verified
projections exist only in the immediate live recheck report and expire with its
controller session/deadline. Future journal-only reads remain descriptive; hashes
and journal integrity cannot recreate issuance. Generic imports still reject
verified field and relationship labels. No journal schema migration or persisted
verification exception is introduced. A capture/import failure is reported as
`source_capture.status: unknown`; task outcome handling stays with the existing
grader. Previously committed observations are not silently retried or rewritten.

All records, fingerprints, reports and derived statistics remain private, including
synthetic-source runs. Canonical live-source reports cannot be relabeled for the
public benchmark exporter. Workers receive neither the source journal nor its
controller capability. No source operation executes a worker or makes a network
request; collection observes work already explicitly requested by the evaluator.

Run `task test:sources:controller` for admission controls and
`task test:sandbox:kernel:controller-sources` for actual pinned workers, the fresh
grader, artifact swaps, context loss and replay controls. The source cases are
also included by installed layouts' `test_source_*.py` selection; native kernel
qualification remains explicitly opt-in. Run
`task test:sources:permission-composition:kernel` for the actual audited
worker/fresh-grader handoff and private-wire, argv and artifact controls.
Synthetic transport supplies the model
responses in these controls; no real inference or native agent is launched.

The full #42/#58 chain still requires supported effective configuration/memory/code
receipts, authenticated cross-source native identity, protected corpus lineage
integration and a reviewed diagnostic-utility study. This slice does not complete
those issues.

The controlled improvement workflow can consume the same owned producer facts for
[implementation prerequisite reporting](implementation-prerequisites.md). The
source and workflow projections share dispatches and native completion; neither
fanout nor a stored report establishes independent evidence or certification.
