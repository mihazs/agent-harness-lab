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
reporting. It permits at most 128 dispatch admissions and a 256 KiB receipt
body, measured on the cached `canonical-compact-json-v1` bytes the scope compares
on every read. Every save checks the bound; an oversized receipt is discarded, later
admissions return nothing and limit loss stays unknown. The byte limit does not describe the full
Python object graph or raw worker output. Reports remain private, including
synthetic runs, and cannot be relabeled for public benchmark export.

A positive implementation packet does not discharge the mandatory certification
requirements. Whole-boundary isolation qualification, independent lineage,
certification evidence and effective runtime/configuration identity remain
unverified. Automatic promotion and adoption authority remain false. Favorable
improvement runs still leave the target unchanged and require independent
certification. This milestone does not complete #7 or #77 or provide an
eligibility issuer.

## Isolation element of `promotion.policy.decide`

The recommendation-only `decide` API derives its isolation element from this
packet instead of a caller boolean. Pass the live scope as
`prerequisite_scope`; `decide` reads it through `implementation_prerequisites`
and reports `isolation: qualified` only when all of these producer facts hold (code
running in the same process with the live scope object is trusted; this is not a
sandbox against in-process callers, but loss is sticky and cached state cannot be
restored by clearing attributes):

- packet `status` is `observed` (every admitted dispatch completed);
- `restricted_process_protocol.status` is `qualified`;
- `permission_result_capture` is `qualified`, so the
  `permission-results-v1` audit was requested and observed no violation;
- `owned_dispatch_completion`, `checked_worker_rootfs` and
  `fresh_grader_artifact_handoff` are all `verified`.

Anything else reports `isolation: unknown`: no scope, an ended, cancelled,
expired, copied or foreign scope, a dictionary packet, a scope lost to the
256 KiB receipt bound, an incomplete dispatch, an unqualified startup protocol,
or an unrequested, unknown or violated permission capture. An unknown isolation
element blocks with the existing `certification_prerequisites_unverified` reason.

The legacy `isolation_verified` keyword is accepted for call-site compatibility
and is declaration-only. It is recorded under `declared_prerequisites` as
`declared_unverified` and never satisfies the isolation element; `True` cannot
assert isolation. Decisions can only become stricter: a call that previously
relied on `isolation_verified=True` now blocks. The other `decide` inputs
(effective runtime verification, grader audit, lineage review and certification
reservation) and every earlier gate are unchanged, so a qualified scope satisfies
the isolation element and nothing else. The `ahl experiment` CLI passes no scope
and reports `isolation: unknown`.

On supported Linux x86-64 permission-audited dispatches, the trusted monitor can
hash the kernel-selected top-level worker executable while it is stopped at exec.
The owned dispatch fact binds this observation to the command and pinned image.
If the executable cannot be read within bounds, the identity remains unknown.
The hash does not establish the identity of a served model, a native agent, a
script, or later descendant executables, and grants no certification authority.

The isolation element consumes the restricted-process qualification of observed
dispatches only. It is not whole-boundary role isolation: the packet's
`mandatory_certification.role_isolation` stays `unverified`, and `decide` remains a
recommendation that never sets automatic promotion. The documented human
residual-risk sign-off for the sandbox boundary and the narrow inference broker
or egress policy for agent credentials remain open in #7.

Run `task test:promotion:prerequisites` for declaration, fake-backend and export
controls. Run `task test:promotion:prerequisites:kernel` for the actual public CLI
with all four roles, shared source ingestion, missing completion, interruption,
GC, copied/imported/replayed scope, stale bindings and deadline controls. These
use synthetic transport without a provider, credential file or native agent.
