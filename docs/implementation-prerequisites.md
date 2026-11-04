# Live implementation prerequisite observations

Controlled `ahl improve --agent responses` runs report implementation facts from
actual owned worker dispatches. The existing runtime image, cgroup and private
disk requirements still apply. `--permission-audit permission-results-v1` remains
optional. Cooperative/native-agent preparation does not acquire these facts.

Audited runs also consume the live [restricted-process startup protocol](runtime-boundary.md)
and block a favorable recommendation when its actual facts are unavailable or
different. A qualified protocol leaves full mandatory certification unverified.

The private `implementation_prerequisites` packet is versioned as
`ahl-implementation-prerequisites-v1`. It distinguishes these observations:

- `owned_dispatch_completion`: admitted workers returned through the actual
  subprocess, private workspace and cgroup producer with terminal cleanup and
  input/storage checks.
- `checked_worker_rootfs`: actual before/after rootfs checks matched the selected
  pin for every completed dispatch. This does not identify a loaded binary,
  serving model or effective instructions/configuration.
- `fresh_grader_artifact_handoff`: an observed executor's selected relative paths
  and byte contents matched a fresh grader's actual fixture and frozen oracle
  inputs. This does not verify semantic oracle validity, filesystem modes,
  unselected files, transient effects or a task's scientific outcome.

The scope begins after the workflow's capability preflight and baseline readback.
It records every subsequent admitted dispatch, including later capability probes.
A private producer stage distinguishes those probes: their deliberate readonly
canary denial remains visible in `capability_probe_permission_results`, while
`permission_result_capture` describes project-role errno results only. Nothing
changes the permission monitor's policy or the existing qualification gate.
Incomplete captures, pending calls, exceptions, changed bindings and exhausted
limits cannot become verified completion.

Actual baseline, staged-candidate, manifest and oracle readbacks bind the packet.
These use the existing integrity-tree digest and raw manifest SHA-256 contracts.
This is content binding within one controller context, not native project identity
or proof that a model consumed the mounted harness. Counts describe admitted
kernel invocations and observed byte handoffs, not independent tasks or roots.
Task-fixture coverage and independence remain unverified.

The existing source consumer and workflow consumer share one owned dispatch fact.
The native monitor completion is consumed once; fanout does not create independent
execution evidence. Source attempts remain per-trial. Source journals still retain
declarations and cannot replay live verification.

Only an unchanged, current in-memory scope in its issuing controller process can
supply this report. Scope exit revokes its private issuance state, including saved
Python contexts; normal fork does not inherit authority. Cleanup uses the original
private context token even when exposed scope fields are mutated. Copies, imported
JSON, fake backend attestations and old/foreign contexts cannot supply authority.
The scope uses the original parent deadline and rechecks its frozen inputs before
reporting. It permits at most 128 dispatch admissions and a 256 KiB projected
receipt body; limit loss stays unknown. The byte limit does not describe the full
Python object graph or raw worker output. Reports remain private, including
synthetic runs, and cannot be relabeled for public benchmark export.

A positive implementation packet does not discharge the mandatory certification
requirements. Whole-boundary isolation qualification, independent lineage,
certification evidence and effective runtime/configuration identity remain
unverified. Legacy `promotion.policy.decide` flags remain caller declarations for
its recommendation-only API; they do not authenticate this packet. Automatic
promotion and adoption authority remain false. Favorable improvement runs still
leave the target unchanged and require independent certification. This milestone
does not complete #7 or #77 or provide an eligibility issuer.

Run `task test:promotion:prerequisites` for declaration, fake-backend and export
controls. Run `task test:promotion:prerequisites:kernel` for the actual public CLI
with all four roles, shared source ingestion, missing completion, interruption,
GC, copied/imported/replayed scope, stale bindings and deadline controls. These
use synthetic transport without a provider, credential file or native agent.
