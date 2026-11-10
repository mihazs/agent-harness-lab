# Evaluation foundation and remaining prerequisites

The current controller is recommendation-only for behavioral changes. Cooperative
smoke evaluation cannot authorize automatic adoption. Existing low-level manifests
remain descriptive. Missing/duplicate trials cannot yield a vacuous passing gate.

## Implemented contracts

The [regression reproduction contract](regression-proposals.md) validates bounded
declared evidence without replaying it or admitting a case to the active bank.
Its receipts preserve unknown snapshots and never label a declaration verified.
Explicit [private bank admission](regression-admission.md) can subsequently accept
an intact controller-reproduced, operator-reviewed case into an immutable
development revision. It preserves unknown native provenance and independence;
oracle correction/invalidation and protected certification remain separate work.

- `ExperimentSpec` freezes task/root/family identities, repeats, seed, resource
  ceiling, effect/noninferiority margins, confidence, family weights and hashes.
  `schedule` produces replayable counterbalanced AB/BA pairs in fresh workspaces.
  Explicit repeated manifests must fit both their own and the caller's trial cap.
  Their `grader_hash` must also match the verified, versioned oracle identity
  before any execution probe. Compute it from the exported manifest with
  `task experiment:oracle MANIFEST=/path/to/manifest.json` and freeze that value
  in the plan. The identity covers complete bundle bytes/modes, grading argv and
  declared artifact selection, including reference/control files. Relocating an unchanged oracle preserves
  the identity; changing it requires a new plan and fresh trials for both arms.
  Old trial IDs cannot satisfy the new schedule. Repeated plans without complete
  bundles must be regenerated; legacy manifests without `experiment` are unchanged.
- `TrialRecord` analysis requires the exact planned trial set, executable graded
  binary outcomes and matching provenance. Infrastructure errors stay unknown.
  Root means retain correlated repeats together. Independent roots are an explicit
  assumption, not inferred from distinct strings or successful tests.
- The fixed analysis uses weighted-root Hoeffding intervals for bounded paired
  differences, with Bonferroni correction over the primary and family endpoints.
  This conservative method needs independent roots. It may be underpowered; a
  narrow interval or benefit is never manufactured from a tiny task set. It does
  not model drift or certify absence of rare harms. Unknown provider costs remain
  unknown. There is no optional stopping or task retry selection.
- The promotion policy separates descriptive results, incomplete/blocked evidence,
  and a recommendation after verified prerequisites. It never sets automatic
  promotion true. A recommendation API call is not an isolation attestation:
  its isolation element comes only from a live qualified
  [implementation-prerequisite scope](implementation-prerequisites.md#isolation-element-of-promotionpolicydecide),
  and the legacy `isolation_verified` keyword is a recorded declaration.
- `TaskSpec` and bank validation freeze fixture/grader bundles, provenance,
  partitions, safety and artifact boundaries. Root leakage is always rejected;
  optional family/project/time separation and copied-fixture checks are enforced.
  [Review declarations and certification qualification](bank-qualification.md) remain
  separate: raw labels never establish certification eligibility or verified
  independence. No current production bank-certification issuer is available. Public
  tasks are explicitly not secret. Grader integrity mismatches reject execution.
  [Controller history](oracle-history.md) can quarantine a grader dependency
  across recorded comparisons while preserving old report bytes. New controlled
  exports also bind current bank admission authority, materialized fixtures and
  observed grader versions through dispatch, sealing and guarded analysis. Dedicated
  [correction audits/review](grader-corrections.md) preserve grader defects as
  grading observations. Immutable corrected-bank replacement remains pending
  in #8. These checks do not authenticate runtime/import dependencies.
- A private controller-owned [managed diagnostic ledger](protected-diagnostic-access.md)
  binds a frozen comparison and consumes access before preparation/worker visibility.
  Its reservations remain explicitly unqualified. The legacy standalone API
  reserves one candidate and consumes access before returning its bank digest. Search roles cannot request access.
  Concurrent access is single-use. API role checks require OS isolation; they are
  not a security boundary on their own.
- Artifact extraction rejects traversal, symlinks, hardlinks and special files,
  and enforces declared file/byte bounds after the worker stops.
- Contained reads open each directory by name from its already opened parent,
  starting at `/`, without following links, so a parent swapped for a symlink
  after the containment check is refused. Bank revision, case evidence and
  lineage snapshot fsyncs, and private journal directory fsyncs, open their
  targets through the same walk. Platforms whose `os.open` lacks `dir_fd`
  support refuse these reads and fsyncs.
- The final file is checked with `lstat` relative to its opened parent before
  it is opened. A device node, Unix socket, FIFO or directory is refused with
  the reader's label (`artifact must be a regular single-link file` for
  artifact extraction, `input must be a bounded single-link regular file` for
  other reads and fsyncs), so a leaf that is already special is never opened.
  A leaf swapped for a special file between that check and the open is still
  opened (non-blocking) and is then caught by the reader's `fstat` check; the
  fsync paths have no such recheck.
- A private journal opens its store once through that walk, creates its lock
  in the open store and publishes the store marker and events from that
  descriptor. Before each publication it walks the store path again and
  refuses unless the path still names the locked directory: a link raises
  `input crosses a symlink`, and a replaced directory raises `case store
  changed while locked`. A swap after that check cannot move the file out of
  the locked store.

## Offline sandbox and threat model

| Role | Visible assets | Forbidden assets |
| --- | --- | --- |
| Proposer | Authorized development evidence, harness | Certification, hidden oracle, host home |
| Evaluator author | Baseline, construction fixtures | Candidate/proposal, prior final feedback |
| Executor | Fixture and selected harness | Oracle, other runs, host credentials |
| Grader | Bounded extracted artifacts, pristine oracle | Project writes, network, credentials |
| Controller | Frozen inputs, access/budget ledgers | Unrelated production changes |

`SandboxBackend` is an opt-in offline Linux/bubblewrap primitive, never a silent
fallback. It builds a mount namespace from explicit system directories, one
workspace and role-allowed read-only inputs; clears inherited environment; and
requests separate network/process/user namespaces, capability removal, process
session cleanup and CPU/address-space/process/file/output/time limits. Input
integrity is rechecked. No host home/config/socket is mounted.
Namespaces do not isolate kernel keyrings, and the sandbox keeps the validator's
uid, so every role and grader launch, like a hook trial, goes through the same
host-side trampoline. It joins a new empty session keyring, then installs a
seccomp filter that fails `add_key`, `request_key` and `keyctl` with `ENOSYS`.
A run therefore cannot search, describe or read the validator's keys, even by
serial number. `/proc/keys` stays mounted, because masking it would stop nested
sandboxes from mounting `/proc`, so a run can still see the type and description
of keys the validator's uid may view. A kernel without key support proceeds; any
other failure stops before bubblewrap starts, and `ahl sandbox-check` reports
`keyring_isolation_unavailable`.
Writable workspaces must be disjoint from every declared read-only input; nested
mounts of the same host tree are rejected before execution. Captured stdout and
stderr share one byte allowance. Timeout, interruption and selector-registration
failures trigger process-group cleanup, including children remaining in the group
after their parent exits. Host process-control tests establish that behavior separately from the
namespace containment tests, which still require a supported host.

The process bound counts only the sandbox's processes, never the invoking user's
others. With `--cgroup-root` it is the worker cgroup's `pids.max`. Without one,
on Linux 5.14 or later, bubblewrap holds the sandbox's first process in its new
user namespace (`--info-fd`, `--block-fd`) while the controller sets
`RLIMIT_NPROC` on it; that kernel counts the limit per user namespace, so the
real UID keeps its host limit. Otherwise, or when that cap cannot be set and read
back, the run continues without a process bound. Each attestation's
`process_bound` names the enforced `scope` (`cgroup` or `user_namespace`) and
limit, or `enforced: false` with a reason, and `ahl sandbox-check` then lists
`no sandbox process bound` among its limitations.

An explicit `--cgroup-root` on `ahl sandbox-check` or `ahl evaluate --agent offline`
opts into aggregate Linux cgroup v2 controls. The caller must supply an existing
unprivileged delegated subtree with cpu, memory and pids controllers already
enabled. No subtree is discovered from the environment, no controller is enabled
by this tool, and no privileged setup is performed. An unavailable or mismatched
resource boundary blocks execution without falling back to rlimits alone.

Each worker gets a new child cgroup with read-back memory and process caps, swap
disabled, grouped OOM handling and a one-CPU bandwidth cap. The child joins before
exec, so descendants inherit membership; the namespace does not mount the control
filesystem. Whole-tree cleanup uses `cgroup.kill`, including children that create
new process sessions. An attestation requires an empty group after cleanup and
complete memory/process/CPU counters. A swallowed memory/process denial invalidates
an otherwise zero exit. Each offline executor and its pristine grader use the
same requested parent policy with separate child groups. CPU bandwidth is a rate
limit, not an exact total CPU-time budget; existing wall-clock limits still apply.
These controls follow the [kernel cgroup v2 interface](https://docs.kernel.org/admin-guide/cgroup-v2.html).

Native synthetic control-file tests validate setup, pre-exec failure, accounting
and cleanup failure handling; they do not establish kernel enforcement. Separate
real kernel canaries are opt-in. On a Linux host with a systemd user manager, run
`task test:sandbox:kernel`: it creates a disposable foreground user scope with
memory/process/runtime ceilings and delegates cpu/memory/pids for that test only.
The native fixture verifies scope identity and exclusive process membership,
moves its controller to a leaf and enables child controllers inside that scope.
Cleanup restores its layout; systemd collects the completed scope. No persistent
unit, privileged service or production controller setting is changed. See
[systemd delegation](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html#Delegate=).

Alternatively, explicitly set `AHL_TEST_CGROUP_ROOT` to an already-delegated
subtree for `task test:sandbox`. No setup is performed on that supplied root.
Default tests visibly skip these kernel checks. Explicit invocation fails on
unsupported or misconfigured hosts. Namespace cases use real bubblewrap; resource
cases separately bypass namespaces to isolate cgroup enforcement. All fixtures
are synthetic. Checks cover role/file/environment/network boundaries, readonly
inputs, artifact links, forged grades, process-session escape, OOM, output limits,
timeouts, interruption cleanup and both arms of all seven offline starter tasks.

A probe whose bubblewrap clone fails with `EAGAIN` reports
`process_limit_exhausted`: the host or the user's own process limit is full,
which is not a missing namespace. A successful bare namespace command does not
establish that requested resource policy. The delegated canary command exercises
the cgroup policy without raising the default limit or adding a runtime fallback.

This is not a complete certified backend: model-backed qualification of the
construction/execution/regrading integration and the complete adversarial
suite remain prerequisites for #7. Per-file rlimits are not aggregate disk quotas;
per-user process limits alone are not cgroup guarantees. Host/kernel compromise is out
of scope. `automatic_promotion=false` remains explicit even if an offline probe
succeeds. Passing selected kernel canaries supports only those tested offline
boundaries, not complete hostile-agent certification or model-backed isolation.

An explicit `--disk-bytes BYTES` on the offline evaluator or sandbox probe adds
quota-backed private storage. It requires `--cgroup-root`, nested unprivileged
user/mount namespaces and `/usr/bin/python3` plus `/usr/bin/bwrap` in the runtime.
The capacity must be a positive multiple of four host memory pages. Half is
allocated to `/work`, one quarter to `/tmp` and one quarter to `/dev/shm` using
separate sized tmpfs mounts. These are shared volume limits across all files and
descendants, alongside the existing per-file limit. tmpfs pages and metadata
also consume the worker cgroup's memory allowance; swap remains disabled.
To cover memfds and additional tmpfs mounts created by nested namespaces, the
cgroup memory ceiling is also capped at the storage budget. Controller-seeded
data pages are measured and subtracted from that ceiling before worker release,
because their memory charge belongs to the controller. The effective ceiling is
the smaller of the requested memory limit and the remaining storage budget;
the attestation records its read-back value. This deliberately couples memory
and storage admission: interpreter/anonymous memory and filesystem metadata use
the same remaining allowance. Small storage budgets may fail before execution.
HugeTLB uses separate accounting on some hosts. An inherited, sealed seccomp
filter denies HugeTLB flags on `memfd_create`, `shmget` and `mmap`, including in
nested namespaces. It validates the syscall ABI and rejects alternate syscall
number ranges. This storage profile supports Linux x86_64 and aarch64; other
architectures or unavailable seccomp support fail closed. Filter identity and
digest are included in the storage attestation. These guards do not audit every
denied syscall and do not enable automatic promotion.
Existing HugeTLB-backed runtime/input mounts, including nested mounts, are
rejected before launch because mapping such a file need not set that flag.
As with the runtime image contract, privileged host mount changes during a trial
are outside this backend's threat model.

A trusted setup process creates the private filesystem and hands its directory
descriptor to the controller through a bounded Unix socket protocol. The
controller seeds it from the validated fixture, then releases a fresh worker
namespace launched from that mount namespace. Before executing the submitted
command, the worker verifies volume capacities and readonly root/input/device
mounts. Setup directory descriptors are closed before worker execution. The host
workspace is never mounted writable in this mode. Failure of setup, verification
or namespace creation blocks execution without a host-directory fallback.

Executor calls must declare their artifact allowlist; offline evaluation uses the
manifest's list. Graders export nothing. After the entire cgroup stops and input
integrity checks pass, successful workers export only those regular, single-link
files through anchored descriptors, refusing symlinks at every component, special
files, missing outputs and a combined size over 1 MiB. Files are validated before
host writes; each replacement is atomic, but a host I/O error can leave a partial
set and fails the trial. Unlisted writes and failed/interrupted worker outputs
are discarded. Source file modes are preserved; new exported files use mode 0600.

`storage_policy` records the capacity split, controller-seeded allocation,
verification and extraction counts;
`aggregate_disk_bound_verified` records successful mount verification. The cap
bounds allocated tmpfs file data, rather than logical sizes of sparse files or
metadata overhead. The memory cap bounds metadata. Swallowed `ENOSPC` errors are
not independently audited, so a verified storage cap does not certify that no
resource access was denied. Automatic promotion remains disabled. Live native
tests exercise all volumes, discarded writes, executable seed permissions,
descriptor closure, bounded exports, links, failures and process cleanup. Unit
tests separately exercise malformed descriptor handoffs and startup deadlines.
Live attacks also try allocating a large memfd and mounting a new larger tmpfs;
both must hit the aggregate memory boundary and invalidate the trial.

Starter grader v2 closes an executable-artifact oracle-read bypass. In an isolated
grader role, submitted Python checks run in a nested bubblewrap namespace with
only readonly runtime libraries and extracted artifacts. It has separate process
and network namespaces and no oracle mount; the pristine parent grader compares
the returned values with its reference. A regression submits code that previously
read the reference outputs and forged a passing check. The frozen grader version
and digests change; old v1 results do not establish this boundary. The explicitly
trusted cooperative diagnostic path still executes trusted controls directly.
Isolated executable checks require `/usr/bin/bwrap` and nested user namespaces;
unsupported execution cannot produce a passing check. This is a contract of the
owned starter graders, not a guarantee about arbitrary caller-supplied graders.

## Pinned offline runtime images

An offline evaluation or sandbox probe can explicitly supply `--image-root ROOTFS
--image-sha256 SHA256`. Both flags are required together. The rootfs is an already
extracted, reviewed private directory; no image is downloaded, unpacked or enabled
automatically. Obtain its content pin without executing image code using
`task runtime:pin ROOTFS=/path/to/rootfs`. This filesystem-manifest hash is not an
OCI registry digest and is never silently derived as an execution authorization.

The root must be owned by the controller with mode 0700. Required regular
directories are `usr`, `usr/bin`, `dev`, `proc`, `tmp`, `work`, `home`,
`home/worker` and `inputs`. Supply the complete intended runtime, including
`/usr/bin/true` for probing, the worker interpreter/libraries and
`/usr/bin/bwrap` for the starter grader's nested checks. Missing dependencies
remain execution failures, never a fallback to host runtime directories.
Keep project files, oracles, credentials and other role data outside the image.

Inspection binds every file's bytes, path membership, mode, numeric ownership,
modification time, symlink target and extended attributes. It does not follow
symlinks during inventory; escaping lexical links, hardlinks, special files and
setuid/setgid entries are refused. Mount points must be real directories.
The bounded profile permits 50,000 entries, 512 MiB of file/attribute data and
8 MiB of serialized manifest metadata, with at most 64 nested directory levels.
Metadata has a separate bound even when
attribute values or files are empty.
No raw paths or file contents enter the runtime-image attestation.

The backend mounts that rootfs readonly instead of host `/usr`/library trees.
Role inputs get a separately populated readonly mount, with a disjoint writable
workspace and private temporary storage. The image cannot overlap workspace,
harness, fixture or oracle trees. Executors and pristine graders use the same
requested image; nested checks inherit its pinned runtime libraries and launcher.
Each worker verifies the expected pin before launch and after termination. A
changed image fails closed; insufficient postflight budget produces an incomplete
integrity result and cannot pass. Hashing consumes the caller's deadline, though
blocking host filesystem I/O remains cooperatively bounded.
The controller must keep the host image tree immutable during runs; pre/post
hashes do not seal it against host writers.

Host kernel and outer bubblewrap remain trusted host components, not part of this
image. Their behavior is not virtualized; the outer launcher hash is separately
recorded. File access/change times and inode identities are not content identity,
and a pin does not prove deterministic execution or protect a compromised host.
Unpinned diagnostic mode remains labeled `runtime dependencies unpinned` and
`automatic_promotion=false` remains universal. Kernel canaries exercise a private
synthetic image, readonly execution, shared pins for both arms and pristine
graders, and the nested Python checks across all seven starter tasks.

`ahl evaluate --agent offline` now integrates fixture-command execution with a
separate grader namespace, selected-harness-only mounts, bounded artifact
extraction, typed grades and the existing paired schedule. Unsupported hosts
write a blocked report before task execution. This is diagnostic infrastructure;
it does not invoke a model or orchestrate proposer/evaluator construction. See the
[offline execution contract](../skills/harness-evaluation/references/execution.md#offline-executor-and-separate-grader-integration).
The mock integration checks exercise both arms of all seven starter tasks, with
broken baseline programs and complete candidate programs. Their outcomes establish
runner wiring, not model quality or actual containment. A supported-host end-to-end
test remains explicitly conditional on the real namespace probe.

`ahl evaluate --sandbox-graders --agent codex` (or `claude`) keeps native agent
execution cooperative while using that same offline artifact-grading boundary.
Every task needs explicit artifacts and a frozen complete bundle. `ahl improve`
and `ahl watch` accept the option too: the controller freezes evaluator-authored
bundles and runs all broken/partial/complete controls offline before paired trials.
Unsupported grading capabilities block before any agent launch. Grader errors,
missing artifacts, changed bundles and malformed typed grades cannot pass or
trigger host fallback. Optional cgroup, image and private-storage selections reach
both control and trial grading; watch fingerprints include those settings.
These synthetic orchestration checks establish wiring, not kernel containment,
inference credential isolation, evaluator independence or improved model outcomes.
Native agents remain cooperative and automatic promotion remains disabled.

[Bubblewrap's upstream documentation](https://github.com/containers/bubblewrap)
explains that application policy and the selected mounts determine the boundary;
the installed binary alone is not a security policy.

## Explicit controller inference

Prepared `ahl evaluate --agent responses` runs can use a controller-owned fixed
Responses endpoint, explicit private key-file/settings and mandatory cgroup,
pinned-image and quota profiles. Model-requested argv commands run offline and
complete bounded working snapshots stay in controller memory between commands.
Only final declared artifacts reach the existing fresh grader. An unavailable
boundary blocks before key loading; ambiguous requests and containment failures
prevent further inference. The same explicit profile isolates `improve`/`watch`
proposer and evaluator-author commands with separate minimal views and histories,
sharing one budget/client through trials and refinement. Controlled authors see
baseline/metadata/methodology only; the controller publishes bounded authored trees
and derives frozen grader commands. Native CLI modes remain cooperative and
automatic promotion remains disabled. Full model-backed qualification is unknown.
See the [settings, cap and state contract](../skills/harness-evaluation/references/execution.md#controller-owned-responses-executor).

Each controlled trial also records private controller observations of tool requests,
validation decisions, reservations, backend invocation and returned output. Controller
IDs and ordering do not depend on model-supplied call IDs. Retention and output are
bounded; omissions, redaction and unavailable streams remain explicit. These records
preserve observed output across later workspace changes, but do not establish semantic
argv authorization, hidden reads, transient writes, provider effects or crash durability.
They are not admitted into sealed offline experiment history or used as a safety grade.

Assistant `output_text` and `refusal` parts are retained separately as
`assistant_emission` observations; other claimed message roles stay unverified.
Each record identifies its response/item/part position, content type and visibility.
At most 64 parts per message are observed, with explicit omission counts and the
existing global record/byte caps. Retained text is bounded to 8,192 UTF-8 bytes,
with credential redaction and truncation reported separately. Invalid Unicode
retains only a bounded replacement and reports `unknown_encoding_loss`,
`encoding_loss=true` and an unknown original UTF-8 byte count.

`protocol_validated` describes response validation, separately from whether quotas
or other checks allowed further dispatch. Observed text can therefore remain in a
denied response's private record. `external_delivery` remains unknown: controller
transport text is neither proof of delivery elsewhere nor a task-success grade.
The same final message can accompany a failed or passing artifact evaluation.
These records may contain private project data and are not public export material.
Run `task test:evaluation:controller-emissions` for native protocol, fidelity and
paired-runner controls; the existing `task test:sandbox:kernel:roles` also checks
emission records with real pinned workers and simulated API transport.

## Frozen controller trajectory assertions

A frozen grader bundle may contain `trajectory.json` to add action compliance to
its independent artifact grade. Artifact-only bundles retain their existing
behavior. The first profile is `schema_version: 1`,
`profile: "controller-trajectory-v1"`, with exact `task_id`, `root_id`, `family`,
`engine`, `outcome_mapping: "artifact_and_required_v1"` and `assertions` fields.
`task experiment:trajectory-engine` prints the required engine object: it pins
every application Python source, the owned evaluator and the Python runtime.
Changing any dependency or assertion requires a new complete bundle digest and
experiment oracle pin, followed by fresh trials in both arms. The candidate
workspace cannot supply or change this policy.

Each assertion has an `id`, Boolean `required`, `severity` (`normal` or `critical`)
and `rule`. The bounded rule language supports:

| Kind | Required rule fields beyond `kind` |
| --- | --- |
| `required`, `forbidden` | `stage`, `tool`, `arguments`, `quantifier` |
| `count` | `stage`, `tool`, `arguments`, integer `minimum`, integer or null `maximum` |
| `sequence` | `stage`, `mode` (`subsequence` or `exact`), nonempty `steps` of `{tool, arguments}` |
| `all_of`, `any_of` | Nonempty `children` of rules |

`stage` selects `request`, `denied`, `dispatch` or observed backend `result`.
`tool` is an exact controller tool identifier, initially `run`; command text
does not create a semantic tool catalog. `arguments: null` compares only the tool.
Exact argv uses `{"mode":"exact","argv":["/usr/bin/true"]}`. Partial argv uses
`{"mode":"partial","argv":{"0":"/usr/bin/true"}}`: keys are canonical
zero-based decimal array indices, and only selected observed entries are compared.
An index outside a completely captured argv array is a known mismatch; a missing
argv observation or an unavailable captured entry remains unknown.
Objects other than these supported argv structures are rejected.

`quantifier: "exists"` requires one matching action. `all` requires a nonempty
domain of the named tool at the selected stage, with every such action matching
the argv predicate; known unrelated tools are excluded. `forbidden` negates the
selected quantifier. An empty complete domain therefore violates required `all`
and satisfies forbidden `all`. Incomplete relevant coverage remains unknown.

Positive witnesses, count lower bounds already reached and observed counts above
a maximum can decide despite omissions. Absence, upper-bound satisfaction and
exact sequences require closed, predicate-relevant coverage. Incomplete or error
responses leave request coverage unknown even if some output items were observed.
Observed requests can still be decisive witnesses. Request, denial, dispatch and
backend-result order use their own controller ordinals; no concurrent
dependency is reconstructed. Missing, redacted, clipped or invalidly encoded
compared arguments stay unknown. A backend result establishes a return observation,
not semantic success. Group truth tables preserve every child: `all_of` fails on
any violation and otherwise needs all satisfied; `any_of` passes on any satisfaction
and otherwise needs all violated. Other combinations are unknown.

Policies reject duplicate/unknown fields, invalid types/numbers, empty groups,
more than 64 KiB, depth over eight, more than 128 predicate nodes or sequences
longer than 32 steps. Receipts are bounded to 1 MiB and identify predicates by
digest and local controller IDs without echoing argv. Live controller handles
bind policy, task, scheduled trial and scope; JSON imports cannot recreate them.
Malformed or unsupported evidence is invalid and unknown.

The frozen outcome mapping requires a valid successful artifact grade and all
required assertions satisfied. A required violation or any critical violation
blocks success; unknown required observations cannot pass. Critical violations
remain visible even when execution or artifact grading fails. The independent
artifact grade is retained alongside the combined controller grade. Optional
noncritical assertions remain diagnostic. The existing paired analysis receives
one outcome per scheduled trial; assertions and tool counts never create roots.
Native identity, semantic independence and history authority remain unverified;
these receipts never authorize automatic promotion or certify hidden reads,
transient writes, external delivery or all effects.

`task test:evaluation:trajectory` runs native synthetic capture, matching, typed
grading, paired-analysis and private-report controls without model/network calls.
The same target runs in wheel and archive verification. See
[controlled experiment reporting](controlled-experiments.md#controller-trajectory-reports)
for saved-result handling and migration limits.

## Interaction contracts for clarification, stopping and no-change tasks

`agent_harness_lab.evaluation.interaction_contract` grades what the evaluated task
agent did at a declared decision boundary, separately from the optimizer's
`no_change` routing. It is opt-in: a task with no contract keeps its artifact-only
semantics, and the module changes no existing grader. This increment is the
contract and a pure grader over synthetic evidence; it is not yet wired into
`run_evaluation.py`, and no runtime is claimed to produce the evidence.

A contract (`profile: "interaction-contract-v1"`, `schema_version: 1`) declares the
`task_id`, an explicit `endpoint` (`whole_task` or `interaction_boundary`, which
freezes what the binary pass means), `required_clarifications` (a subset of
`allowed_clarifications`), `permitted_mutations`, `protected_paths`, an optional
`stop_reason` and a `completion` of `{"kind":"final_state","paths":{path:sha256}}`
or `{"kind":"interaction"}`. `contract_sha256` is the identity to store with the
task; any change invalidates earlier comparisons.

`evaluate(contract, evidence)` takes runner-observed evidence: `capture` states
whether the `transcript` and side-effect `receipts` are `complete`, `partial` or
`missing`, and how the run `termination` ended; `events` are `clarification_request`,
`clarification_reply`, `mutation`, `stop` and `claim` records; `state` holds initial
and final digests. It returns three-valued `interaction_requirement_satisfied`,
`whole_task_completed` and `state_preserved`, plus `passed` under the endpoint.
Asking at a declared boundary can satisfy the interaction without marking the
task complete, and ask, reply, act passes only after the final-state postconditions
hold. Violations are named separately (`missed_necessary_clarification`,
`unnecessary_clarification`, `acted_before_clarification`, `prohibited_mutation`,
`protected_state_changed`, `stop_not_deliberate_or_not_authorized`,
`abnormal_termination`).

Absence is concluded only from a `complete` channel. A missing transcript, partial
receipts, a missing final state, or a termination of `unknown` or
`unsupported_continuation` leaves the result unknown, and a crash, timeout,
cancellation or provider error fails the interaction whatever the contract declares
and is never a deliberate stop. A natural-language `claim` is retained but never
satisfies a postcondition. Clarification topics and stop reasons are identifiers
that a trusted runner assigns to observed events; the grader does not judge free
text, and semantic adjudication remains a separate step. Receipts never authorize
publication or promotion. Replay fixtures, scripted user replies and evaluator
integration remain later work under the same issue.

## Offline validation and limits

The [paired adversarial development bank](adversarial-bank.md) adds frozen policies
for final artifact effects, separate completion counts and critical-effect vetoes
in private offline experiment history. Required missing observations remain unknown.
Transient accesses, native activation, model compliance and external delivery are
outside this component's observed scope.

The tests cover exact coverage/provenance rejection, clustered repeats, critical
harms, a large synthetic benefit, conservative small-sample abstention, deterministic
scheduling, task/oracle integrity, split leakage, private single-use certification,
artifact escapes and unsupported capability behavior. A predeclared seeded null
check runs 100 synthetic experiments with 40 independent roots and correlated
within-root repeats; it permits at most 10 false promotions and observes zero.
Zero of 100 has a 95% Wilson upper bound of approximately 3.7%, not zero risk.
This is a falsification check under those assumptions, not general calibration.

The implementation stages for #7/#8/#9 remain tracked: a reviewed heterogeneous
executable task pack, grader falsification/mutants, manifest generation, full
sandbox integration, precision-planning simulations and independently graded
live certification are not claimed completed by these interface tests.

CLI: `ahl sandbox-check`, `ahl bank --manifest BANK.json --list`, and
`ahl experiment --spec SPEC.json --records RECORDS.json`. No command purchases
compute, downloads benchmarks or starts a background service.

## Reproducible starter bank

The bundled `starter-v1` pack adds seven small synthetic final-state tasks and
29 grader-control checks. It includes an executable calculator repair, preservation
checks, recovery/final-state handling, an instruction conflict, a skill-routing
decision probe, queue resumption and adversarial text as data. The routing probe
does not demonstrate actual skill activation. The pack is a deterministic starter
and falsification suite, not a representative coding benchmark or sufficient
certification sample. Related calculator cases share a root. All cases remain
`independence=unverified`; no protected certification/transfer population is
manufactured by relabeling these variants.

Broken, partial, complete and alternative-valid controls are provided for every
grader. The calculator also rejects a superficial output-only repair while grading
the function on additional inputs. Graders use a typed outcome envelope; invalid
references, malformed envelopes, contradictory exit codes and missing outcomes are
grader defects, not baseline task failures. Declared complete bundles (including
reference data and controls) are hashed before execution and checked afterward.
This detects changed declared inputs. The optional audit regrading path below
advances #7; full task-runner integration still remains.

`ahl bank --starter --list` validates and lists the pack. It exits 3 because task
independence remains unverified. `--audit --cooperative-controls` explicitly runs
the trusted synthetic grader controls with a minimal environment, without a model.
This is cooperative diagnostic execution, not sandbox evidence. Alternatively,
`ahl bank --starter --audit --sandbox-controls` probes the offline bubblewrap
backend and returns an explicit `blocked` report (exit 3) when unavailable, without
falling back to host execution. On a supported host, each control is materialized
in a disposable worker directory, then only its declared bounded artifacts are
copied to a fresh read-only grader input. A separately copied, hash-checked oracle
bundle is mounted read-only; the grader gets a fresh scratch workspace, a cleared
environment and no network. The calculator's allowlist includes its repaired
source file as well as its structured result and preservation artifact. Claimed
worker grades are ignored. Typed grades must agree with process status; timeout,
output limits, malformed grades and changed inputs cannot pass. Per-control
execution attestations remain in the private audit result.

This mode currently supports the starter-style `grade.py.txt`/`expected.json`
Python grader contract using `/usr/bin/python3` inside the namespace. It does not
isolate a model run or provide inference access. This audit entrypoint uses the
default host runtime and writable workspace profile; the optional pinned-image
and private-storage profiles above are exposed through the offline evaluator and
sandbox probe. Whole-experiment parent-budget orchestration and full role
integration remain pending. Cgroup controls alone cover aggregate memory/process
resources and CPU bandwidth; storage caps require the explicit storage profile.
Offline audits now use one 300-second wall-clock
admission deadline shared by validation, capability probing, materialization,
extraction, oracle copying and all control executions. API callers can pass their
remaining `budget_seconds`; individual subprocess limits are capped by what
remains. Exhaustion and grader timeout reports retain completed checks and never
claim all controls passed. In-process filesystem operations and cleanup are
accounted for but cannot be preempted at an exact deadline; this is not an
enforceable provider cost/token or aggregate CPU limit. Mock-backend tests
exercise extraction, typed grading and all 29
controls; they do not prove containment. The live test is explicitly skipped when
the namespace probe fails. Neither audit mode authorizes promotion.

`--export NEW_DIRECTORY` writes a descriptive manifest with bounded fixtures,
grader bundles, control paths, task/root identities and an explicit no-promotion
flag. It never launches an agent. Source materialization is confined to declared
bounded `app/*.py` fixture paths and rejects traversal/symlinks.

The bank is reproduced by `scripts/build_synthetic_bank.py`, including under a
private umask. Its source programs are packaged as data and materialized only
inside disposable fixtures, avoiding installation-generated bytecode changing
frozen bank inputs. Both wheel and versioned archive include the complete pack.
These checks establish reproducibility and objective control behavior; fresh-model
performance, semantic independence and hidden-oracle isolation remain unexecuted.

Task-bank validation also checks [versioned content exposure](content-grouping.md)
without treating lexical similarity as verified lineage.

The optional [permission-result audit](permission-audit.md) observes a bounded
errno policy in supported pinned Linux workers. It keeps task outcomes separate
from permission qualification and does not certify hidden access attempts.
