# Evaluation foundation and remaining prerequisites

The current controller is recommendation-only for behavioral changes. Cooperative
smoke evaluation cannot authorize automatic adoption. Existing low-level manifests
remain descriptive. Missing/duplicate trials cannot yield a vacuous passing gate.

## Implemented contracts

- `ExperimentSpec` freezes task/root/family identities, repeats, seed, resource
  ceiling, effect/noninferiority margins, confidence, family weights and hashes.
  `schedule` produces replayable counterbalanced AB/BA pairs in fresh workspaces.
  Explicit repeated manifests must fit both their own and the caller's trial cap.
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
  promotion true. A recommendation API call is not an isolation attestation.
- `TaskSpec` and bank validation freeze fixture/grader bundles, provenance,
  partitions, safety and artifact boundaries. Root leakage is always rejected;
  optional family/project/time separation and copied-fixture checks are enforced.
  Unknown semantic independence/review blocks certification eligibility. Public
  tasks are explicitly not secret. Grader changes invalidate comparisons.
- A private controller-owned certification ledger reserves one final candidate and
  consumes access before exposing its bank. Search roles cannot request access.
  Concurrent access is single-use. API role checks require OS isolation; they are
  not a security boundary on their own.
- Artifact extraction rejects traversal, symlinks, hardlinks and special files,
  and enforces declared file/byte bounds after the worker stops.

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
Writable workspaces must be disjoint from every declared read-only input; nested
mounts of the same host tree are rejected before execution. Captured stdout and
stderr share one byte allowance. Timeout, interruption and selector-registration
failures trigger process-group cleanup, including children remaining in the group
after their parent exits. Host process-control tests establish that behavior separately from the
namespace containment tests, which still require a supported host.

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

The default probe can fail on a busy shared account because its `RLIMIT_NPROC`
is per real user, not per trial. A successful bare namespace command does not
establish that requested resource policy. The delegated canary command exercises
the cgroup policy without raising the default limit or adding a runtime fallback.

This is not a complete certified backend: aggregate disk quotas, a narrow inference broker,
full construction/execution/regrading integration and the complete adversarial
suite remain prerequisites for #7. Per-file rlimits are not aggregate disk quotas;
per-user process limits alone are not cgroup guarantees. Host/kernel compromise is out
of scope. `automatic_promotion=false` remains explicit even if an offline probe
succeeds. Passing selected kernel canaries supports only those tested offline
boundaries, not complete hostile-agent certification or model-backed isolation.

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
The bounded profile permits 50,000 entries and 512 MiB of file/attribute data.
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

[Bubblewrap's upstream documentation](https://github.com/containers/bubblewrap)
explains that application policy and the selected mounts determine the boundary;
the installed binary alone is not a security policy.

## Offline validation and limits

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
isolate a model run or provide inference access. Aggregate disk quotas,
pinned dependency images, whole-experiment parent-budget orchestration and full
role integration remain pending. Opt-in cgroup controls above cover aggregate
memory/process resources and CPU bandwidth, but do not provide disk quotas.
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
