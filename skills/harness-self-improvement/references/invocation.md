# Invoking harness self-improvement

Use this routing for an explicit request in an existing Codex or Claude session.
The outer host selects the skill; the project's trusted `ahl.json` configuration
selects the AHL runtime. Without `ahl.json`, AHL's documented policy picks the
single installed runtime or the invoking host's runtime, else asks once. Do not
force the host's native evaluation runtime; never choose one yourself.

## Standalone project invocation

Confirm the exact requested project, or use the current project when none is
named. Run from that project so its configuration and relative paths resolve
there:

```sh
ahl skills invoke . --scope project
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
- measured durations and remaining budget, in the receipt (`evaluation_construction_seconds`
  and each repair's `elapsed_seconds`, also inside `recovery.json`) and in private run
  artifacts, and the `origin` and `parent_run_id` invocation context given to
  preparation children. Each route times its own work, so these values differ by
  design; every field derived from a clock must be listed in `entry_parity.py`
  (a guard test derives them from the code), and no other field may differ;
- `invocation.json`, written only by the skill route as soon as its run directory
  exists, including a run that the configuration preflight blocks.

Stdout is the receipt and also `workflow.json`. Stderr matches except for the
run directory and elapsed times. A managed-preparation marker denies both routes
with identical output. Treat any other difference as a defect to report. The
synthetic parity tests do not establish native-host compliance.

A zero-spend `blocked` preflight whose `remediation` is a user choice (the
`choice` field of `preflight_plan` lists the options) is the one allowed
re-dispatch: ask the user the single question, then rerun the same
`ahl skills invoke` once with the answer appended (`--agent <choice>`), without a
second configuration step. Offer to remember the answer; append `--remember-runtime`
only if the user says yes (`ahl config show`/`clear` manage it; the skill never
writes a project file for it). Relay `remediation` verbatim. To use the user's past
sessions as evidence pass `--history [--since DATE|first]`; relay a `history_*`
block's `remediation` verbatim and never assert recurrence the receipt does not
report. A request the command cannot satisfy must be reported as unsupported, not
silently dropped.

After this attempt, do not retry through `improve`, another adapter or manual
model trials. Never fall back after a charged or ambiguous attempt. Retain its
private artifacts and report the concrete remaining requirement. Recovery must
use the controller's supported contract and cannot reset consumed budget or
invent application by editing the target manually.

After an interruption, do not restart through another adapter or manual trials.
If the private output uses the default run directory, run `ahl skills invoke . --resume RUN_ID` with its final directory name. Recovery can finalize a completed
no-change proposal whose receipt was not yet published; it checks the project,
configuration and harness snapshot and never repeats a model call. Other stages,
including an ambiguous in-flight action, require intervention and are not replayed.
Keep the original private artifacts. Recovery does not reset the consumed budget
or authorize a target edit. Custom output directories cannot be resumed. A hard
crash may leave a lock; verify its recorded PID before removing a stale lock.

## Scope mapping

The command's `--scope` selects the harness layers; the user's words choose it and
the skill never widens it. Only the command line selects global scope: a project
file cannot. The skill writes the scope out on every run, `project` included, so a
default never decides it.

| The user says | Run |
| --- | --- |
| nothing about scope, "this project", "my repo" | `--scope project` (never reads or writes user-level files) |
| "global", "my user-level/global harness" | `--scope global --agent codex\|claude` |
| "both", "global and project", "everywhere" | `--scope project+global --agent codex\|claude` |
| could be either layer ("my skills", "my setup") | one question, or a `project` run |

A global scope names its runtime: use the one the user stated, else ask once; never
infer it from the invoking host. Add `--repair-contracts` only when the user asks for
the deterministic skill-header repair (a no-model, journaled and reversible change).
Without it a user-level layer returns `blocked` with
`global_scope_model_evaluation_unavailable` and changes nothing: report that status,
its `remediation` and each layer's row, and offer the repair as the user's choice.
Never do the global work by hand once the CLI path exists; the manual rules below
apply only before any attempt, when the CLI is unavailable. Report each layer's row
from the receipt, relay its `rollback` command, and never call a blocked, exhausted or
failed layer a success.

## Loop routing

`ahl skills invoke` runs the evaluate > improve > evaluate loop by default whenever `ahl.json` pins a task bank, with the
same receipt as `ahl improve`. The user's words choose the loop flags and nothing widens them: a bar comes only from a
number the user gave, and the scope is still written out as in the table above.

| The request | Route |
| --- | --- |
| "improve until the score is 0.8", "keep improving until it reaches 80%" | `ahl skills invoke . --scope project --accept-score 0.8` (the user's number as a fraction; add `--max-rounds N` only when the user gave a round cap) |
| "improve my harness", "fix my agent instructions" (no score) | `ahl skills invoke . --scope project`; the receipt labels the bar `acceptance_source: default` |
| "evaluate my harness only", "don't change anything" | No `ahl skills invoke`: the loop improves when the baseline misses the bar. Offer `ahl analyze .` for read-only evidence and say a baseline score alone has no command |
| "how good is the harness for UX/UI", "score my coding area" | `ahl score --area X` when `ahl --help` lists `score`; otherwise say per-area scoring is not available and never estimate an area by hand. A per-area bar is a `loop.accept_score` map of declared area ids |
| "fix the failing checkout test", "improve the page load time", "rename this variable" | Not a harness request: do not invoke |

After dispatch, quote `loop.stop_criterion`, `loop.acceptance_source`, `loop.remediation` and the exit code as printed. Say
`accepted` only as "meets the declared bar on the frozen development and regression evidence": the receipt carries
`certification: unverified` and `automatic_promotion: false`, and nothing was applied. A `blocked` result with
`loop_requires_frozen_bank` needs a pinned bank in `ahl.json`; relay its `remediation` and do not retry with `--single-pass`
(deprecated: the previous improve-first pass, removed after one release, whose receipt says `loop.status: not_used`).
A receipt whose `loop.status` is `not_used` made a single pass: report that, with its `loop.reason`.
A single pass that ends `applied` installed its evaluated candidate in the project: relay `changed_files`, `partial_improvement` when present
and the `rollback` command as printed, and say that `application.certification` is `not_established`.

## Report integrity

The receipt holds the facts a final report may state. Hazards it closes, and the rule for each:

- **Seeded run.** `provenance.agent_supplied_hypothesis` lists the digest (never the text) of every
  `--focus` or `--from-audit` hint and of the static convention a first run stages by itself (`source: static_audit`).
  Because the proposer saw it, `provenance.candidate.independent_corroboration`
  is `false` for every candidate of that run. Never describe a match as an independent finding; say
  the candidate was proposed from a hint you or the user supplied.
- **Evidence strength.** `evidence_strength.label` is `behavioral_proof`,
  `source_correction_without_behavioral_proof` or `insufficient`. `behavioral_proof` needs the baseline to
  reach valid verdicts on the targeted tasks, at least `minimum_baseline_failures` baseline failures, a
  paired repair under the frozen inputs, and one adjacent-regression or transfer control. Quote the label
  and its `reasons`; do not upgrade it. Without a regression control say "regressions not tested" and list
  `controls_not_run`; "no regressions" is reportable only when `regression_statement` says a control ran.
- **Frozen grader.** `grader_changed_after_trials` (reason or `evidence_strength.reasons`) voids the
  comparison for adoption. Fresh trials under the new grader are a new comparison; do not regrade both arms.
- **Numbers.** Every elapsed time, count, rate and denominator is written `N [receipt: dotted.path]`,
  copied from a receipt field with its denominator, or `unknown`. A "prior sessions" count excludes the
  current session. Check a draft with `python3 -m agent_harness_lab.report_lint REPORT.md workflow.json`.
- **Continuation.** After a terminal result, "continue" authorizes re-running the same controller
  with more evidence (for example `--history`, another `--focus`), not manual trial rigs. A model trial run
  outside the controller is reported as `outside_budget_unverified` and counts against the user's
  authorization for model calls.
- **Mechanism coverage.** `mechanism_coverage` lists the mechanisms the proposer named as unverified
  hypotheses. `coverage.open` entries are not covered, whatever happened to the candidate, and
  `coverage_complete: false` means unseen mechanisms are possible. Do not say a mechanism was repaired
  or diagnosed from this field; quote `admission[].rejection_reasons` for entries the contract refused.
- **Preparation outcome.** `preparation.outcome` and its `reason_codes`, `missing_evidence` and `baseline_checks_passed` are
  the preparation agent's own declaration against a fixed vocabulary (`controller_verified: false`). Quote them as
  declared; `no_change_supported` is not a claim that the harness is correct, and `spend_label: spent_without_evaluation`
  means the stage spent budget and no trial was evaluated.
- **Provider-reported cost.** `model_calls[].provider_reported` is the runtime's own duration, turns, cost and token usage for an
  owned preparation or construction call (`provider_reported_unverified`). Quote it per call as reported; never sum it, call it AHL's cost, or
  read an absent field as zero.
- **Pre-edit check.** Before staging or applying an edit to a harness file, detect the checkout's forge with
  `ahl sources detect PROJECT`, then use that forge's read-only CLI or API to check open pull or merge requests
  touching it. Check local uncommitted changes too. An unavailable forge, failed command or unknown provider
  is not an empty result: report `open_pull_requests: not_checked`. The controller's `pre_edit_check` covers
  local Git status only.

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

## Review and apply a saved plan

A repair run that stopped before writing (`interrupted`, `budget_exhausted`) or was rolled back keeps its
`change-plan.json` in the run directory its receipt names. `ahl apply RUN --project PROJECT` prints a review packet
and writes nothing; it makes no model call and spends no budget. Relay its `status`, `blockers`, `operations`,
`binding` and `evaluation` as printed: `evaluation` is the saved report's own claim, never verified here.

Pass `--authorize-plan PLAN_SHA256` only after the user, having seen the packet, confirms that exact plan. The
original request, a resume or a passing check is not that confirmation, and approval is not certification: a
behavioral plan stays `blocked`. `applied_verified` means the file set and modes were re-read; activation and
behavior stay unknown. Report `stale` or `blocked` as printed; do not edit files to make a plan match. An
interrupted authorization prints the command to repeat (it applies once, or reports `stale` when the reviewed
bytes are already installed) and any unfinished deployment to finish with `ahl promotion`.

## Assisted fallback before dispatch

If the CLI or adapter is unavailable before any attempt, report this and follow
the manual loop below within the authorized scope and remaining budget.
Label this an assisted path with its own verification limits; it does not inherit
an authenticated CLI application result. Use executable evidence for applied
changes and name unavailable runtime/evidence prerequisites. Do not bootstrap a
new runner or silently install tools to simulate adapter availability.

The manual fallback keeps the scope and authorization rules: the project is the
default, and user-level files are in scope only on an explicit global request or a
named global location, with that exact scope and its snapshot, evaluation and
rollback boundaries. Project invocation does not authorize global discovery links,
global instructions or another project's changes. Once any command attempt exists,
the fallback is closed and the scope mapping above applies.

### The manual loop

1. Snapshot affected files with modes and hashes before changing them. Preserve
   baseline separately from candidate; capture applicable ambient configuration.
   Before touching a surface other than root instructions or skills, check its
   writability in the [surface matrix](surfaces.md).
2. Create a small task set from the failure and acceptance criteria. Include an
   adjacent regression and a fresh transfer case. Freeze graders before scoring.
   Use real task execution when the claim concerns execution; hypothetical answers
   are only decision probes. Keep held-out cases out of candidate feedback.
3. Select the next high-impact uncovered mechanism. Prefer a localized correction
   or simplification; change one causal component at a time unless callers must
   change together. Retain unique useful behavior. Test interaction regressions
   before adopting a combination of independently eligible edits.
4. Run baseline and candidate with comparable resources using the [evaluation methodology](evaluation.md).
   Inspect failures, traces and artifacts. Repair the evaluator if it is wrong,
   version it, and rerun both sides; never relax a criterion to make a patch pass.
5. Refine a failed candidate from the observed failure. Reject regressions, apply
   independently successful edits, and test the final candidate on fresh held-out
   cases. A held-out case used for refinement becomes development data.
6. Validate skill metadata, references, scripts, activation and negative routing.
   Apply eligible changes without another approval question, then smoke-test the
   installed version. Record exactly what changed and the supporting outcomes.

Default shared ceiling: 24 agent trials and 45 minutes including nested evaluations.
Use at most three candidate variants and one refinement per mechanism, within
that shared ceiling; these are search limits, not a limit of three fixes. Allocate
trials across material mechanisms before repeating easy successes, and reserve
about a quarter for final composition, safety and transfer checks. Stop when the
coverage table is resolved or the shared budget is exhausted; report untested
families as deferred, never as disproved. A smaller user budget takes precedence.
Do not recursively launch unbounded improvement sessions to evaluate this skill:
use controlled fixtures and count all child trials in the same budget.
