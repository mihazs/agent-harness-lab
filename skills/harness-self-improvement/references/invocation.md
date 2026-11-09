# Invoking harness self-improvement

Use this routing for an explicit request in an existing Codex or Claude session.
The outer host selects the skill; the project's trusted `ahl.json` configuration
selects the AHL runtime. Do not force the host's native evaluation runtime.

## Standalone project invocation

Confirm the exact requested project, or use the current project when none is
named. Run from that project so its configuration and relative paths resolve
there:

```sh
ahl skills invoke .
```

The adapter enters the existing `improve` controller once, using its configuration,
authentication, shared budget, evaluation, application and receipt contracts.
Forward supported options unchanged; use `ahl skills invoke --help` for the current
options. The selected configuration can reject incompatible overrides. Resolve
missing setup through the existing readiness workflow before dispatch; retain the
selected provider, model, permissions and runtime.

A configured Responses runtime may apply changes when the existing signed
authorization and applicable evidence gates permit it. Invocation adds no
authorization or certification evidence. The `origin='skill'` metadata describes
the entrypoint only.

Preserve the command's stdout, stderr and exit status, including emitted JSON.
Explain its recorded status, reason, changed files and application verification
without replacing the result or changing a nonzero exit's meaning. Candidate
files and successful child exits alone do not establish application. Report
applied, blocked, failed, exhausted, no-change or intervention outcomes from the
actual controller result and identify checks it did not execute.

Keep workflow output, hashes, traces and project-derived reports private. A
private JSON result is not a public receipt. The controller owns target
verification and rollback records.

## Receipt parity with `ahl improve`

For the same project, frozen inputs and runtime, `ahl skills invoke PROJECT`
and `ahl improve PROJECT` return the same exit code and the same receipt keys and
values. This covers status, reason, recommendation, `decision`, change plan,
prerequisites and changed files. `agent_harness_lab/entry_parity.py` lists the
only allowed differences:

- each route's private run directory (`output`), with paths under it compared
  relative to it;
- the `deployment` ID minted by each application and the `rollback` command that
  embeds it; each route's own deployment must roll back to the same target bytes;
- digests that bind run artifacts holding timing or context values:
  `plan_sha256`, `decision.bindings.results_sha256` and `manifest_sha256`,
  and the change plan's `affected_controls_sha256` and `evidence` reference;
- measured durations and remaining budget in private run artifacts, and the
  `origin` and `parent_run_id` invocation context given to preparation children;
- `invocation.json`, written only by the skill route as soon as its run directory
  exists, including a run that the configuration preflight blocks.

Stdout is the receipt and also `workflow.json`. Stderr matches except for the
run directory and elapsed times. A managed-preparation marker denies both routes
with identical output. Treat any other difference as a defect to report. The
synthetic parity tests do not establish native-host compliance.

After this attempt, do not retry through `improve`, another adapter or manual
model trials. Never fall back after a charged or ambiguous attempt. Retain its
private artifacts and report the concrete remaining requirement. Recovery must
use the controller's supported contract and cannot reset consumed budget or
invent application by editing the target manually.

## CLI-managed preparation

When a parent supplies context.json or a candidate/evaluation stage contract,
follow it instead of the standalone route. Use its run/stage identity, scope,
runtime, configuration/authentication references, output paths, absolute deadline
and trial ceiling. Keep working directory and relative paths tied to that context;
these references grant no additional authority or budget. Return the required
artifacts and metadata at assigned locations. Preserve frozen evaluations;
refinements need fresh held-out inputs. The parent schedules trials and owns
application checks and real project writes.

Do not run `ahl skills invoke`, `ahl improve`, `ahl watch`, `ahl evaluate`, agent
runners or nested model trials from a preparation child. Do not read ai-memory,
alter global settings or permissions, commit, publish or deploy inside that stage.

The presence of `AHL_MANAGED_PREPARATION`, including an empty value, denies
adapter/controller/evaluate entry to prevent accidental recursion. It grants no
authority or budget and proves neither isolation nor native-agent compliance; a
cooperative child can remove it. Missing parent context requires reporting that
gap, not inventing paths or permission. Synthetic child tests establish routing
behavior only, not native-host compliance.

## Assisted fallback before dispatch

If the CLI or adapter is unavailable before any attempt, report this and follow
the entrypoint's manual loop within the authorized scope and remaining budget.
Label this an assisted path with its own verification limits; it does not inherit
an authenticated CLI application result. Use executable evidence for applied
changes and name unavailable runtime/evidence prerequisites. Do not bootstrap a
new runner or silently install tools to simulate adapter availability.

Automated global scope is unsupported. An explicitly authorized manual global
task retains its exact named scope, snapshots, evaluation and rollback boundaries.
Project invocation does not authorize global discovery links, global instructions
or another project's changes.
