---
name: harness-self-improvement
description: Evaluate and improve agent instructions and skills when the user explicitly asks to improve, fix, or maintain a harness. Handles natural-language requests and named invocation; never runs unsolicited during ordinary coding.
---

# Harness self-improvement

Requires `ahl` 0.169.0 or later. If `ahl --version` is older, or `ahl doctor` prints an
`installation` WARNING, tell the user to run `ahl update` before dispatching.

## Invocation mode

Choose the mode before following the manual steps below. Read
[invocation routing](references/invocation.md) for the command and result contract.

- In a CLI-managed candidate or evaluation preparation stage, follow the parent's
  context.json and stage contract. Work only in its assigned paths; do not invoke
  the skill adapter, improve, watch, evaluate or nested trials, or write the real
  project. The parent owns execution and application. The presence of
  `AHL_MANAGED_PREPARATION` denies recursive entry; it grants no authority.
- For an explicit standalone project invocation, run `ahl skills invoke [PROJECT]`
  once using that project's existing configuration, authentication and shared
  budget. PROJECT defaults to the current directory. Preserve the command's
  stdout, stderr and exit status, then report its actual result. After dispatch,
  do not start a manual loop or repeat the invocation to bypass a blocked,
  failed, exhausted or ambiguous result. One exception: a `blocked` preflight
  that spent nothing and whose `remediation` is a user choice (for example
  `select_one_runtime_with_agent`) is not a failed run. Ask the user that one
  question, then re-dispatch once with the answer using the exact command in
  `remediation`; a second block is final. Say that an explicit invocation
  authorizes the bounded default run and that `execution_authority: false`
  on a blocked plan means only that nothing was dispatched. To use the user's
  past sessions as evidence, pass `--history [--since DATE|first]`; relay a
  `history_*` block's `remediation` verbatim and never assert recurrence it
  does not report.
- Automated global improvement is unsupported. An explicit global request or
  named global location retains the manual scope and authorization rules below;
  do not route it through the project adapter.
- If the CLI is unavailable before any attempt, use the assisted manual fallback
  in the reference and state its verification limits.

Complete the improvement loop: diagnose from evidence, construct an evaluation,
execute baseline and candidate, repair failures, and apply supported improvements.
A failure description or proposed candidate is optional. Discover improvement
candidates from available traces, evaluation failures, current instructions and
source contracts yourself. Do not require the user to identify what went wrong
before beginning. An optional focus hint does not replace coverage of other
material mechanisms. When capture is missing, investigate current behavior and
mark historical recurrence unknown rather than inventing it.

A request to improve the harness authorizes this reversible work within its named
scope. Do not ask again to build tests, run local evaluations, refine a candidate,
or apply an eligible edit. Missing evaluation infrastructure is work to do, not a
reason to return a proposal. Load `harness-evaluation` for the execution procedure.

## Scope and evidence

Default to the current project's instructions and project-owned skills. Global
skills require an explicit global request or a named global location. Preserve
managed content, permissions, model configuration, unrelated changes, and external
systems. Do not commit, publish, deploy, or write routine memory as part of this
workflow. Higher-priority execution restrictions still apply.

Locate effective instructions, skill metadata, active paths and shadowed copies.
Check Git state and existing PRs when there is a repository. A non-Git skill folder
uses file snapshots; it does not require a new repository. Use targeted ai-memory
retrieval with verified scope; read relevant pages and, when needed, raw session
observations. Missing history does not prevent current-source investigation.

Read actual failed commands, artifacts and completion reports, not just summaries.
For each material issue record a short chain:

- Observed failure and source (or explicitly labeled hypothesis).
- Responsible component and competing explanation, including evaluator failure.
- Smallest candidate edit and predicted observable difference.
- Reproduction, adjacent regression, and reversal.

Do not infer recurring failure from a single anecdote. Conversely, do not demand
historical statistical proof to reproduce and repair a current defect. If baseline
already succeeds, investigate whether the test is too easy or the proposed rule is
redundant; do not accumulate advice merely because it sounds useful.

## Diagnose the portfolio before selecting a repair

For a broad audit or historical request, map distinct failure mechanisms before
editing. Use [trace diagnosis](../harness-evaluation/references/trace-diagnosis.md)
when history contains multiple sessions or repeated interactions. Keep a compact
coverage table: mechanism, independent case support, source/trace evidence,
competing explanation, responsible harness component, candidate, falsifier and
disposition. Separate tool/runtime defects, data gaps and already-fixed behavior
from instruction defects. Include rare severe failures, successful controls and
unclassified cases; frequent events alone do not define importance.

Allocate discovery across relevant time periods, task types and affected harness
components. Inspect representative and contradictory cases per material family;
expand a thin family before rereading a well-understood one. Use retrieved snippets
for routing and raw evidence for conclusions. Do not equate bytes read, sessions
counted, prompt length or number of edits with diagnostic depth.

One passing repair closes its mechanism, not a broad request. Continue with the
other material families while budget remains. Each ends as applied, evaluated and
rejected, already covered, outside authorized scope, or deferred with a concrete
missing fact/resource. Do not silently drop a family because the first candidate
passed or another candidate tied the baseline. Merge families only when the same
cause and repair explain their evidence. Prefer several independently justified
small corrections over a quota of edits or a wholesale rewrite.

## Execute the loop

1. Snapshot affected files with modes and hashes before changing them. Preserve
   baseline separately from candidate; capture applicable ambient configuration.
   Before touching a surface other than root instructions or skills, check its
   writability in the [surface matrix](references/surfaces.md).
2. Create a small task set from the failure and acceptance criteria. Include an
   adjacent regression and a fresh transfer case. Freeze graders before scoring.
   Use real task execution when the claim concerns execution; hypothetical answers
   are only decision probes. Keep held-out cases out of candidate feedback.
3. Select the next high-impact uncovered mechanism. Prefer a localized correction
   or simplification; change one causal component at a time unless callers must
   change together. Retain unique useful behavior. Test interaction regressions
   before adopting a combination of independently eligible edits.
4. Run baseline and candidate with comparable resources using `harness-evaluation`.
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

## Evaluation recovery and adoption

Follow the evaluation skill's recovery ladder before declaring an execution path
unavailable. No benchmark, empty memory, unavailable labels, an unrelated connector
error, or inability to replay the exact historical model is not a terminal condition.
Build the smallest valid experiment and narrow the claim to what it measures.

Adopt a behavioral edit when paired evidence supports its intended benefit and the
relevant regression checks pass. Direct source/contract corrections may be adopted
with executable or structural verification without claiming increased model success.
Qualitative changes may use blinded, evidence-citing comparisons of actual outputs;
label that evidence weaker than objective acceptance. Self-praise is not a grader.

A genuine authentication outage, denied capability, or exhausted budget cannot be
reported as a passing evaluation. Complete independently verifiable repairs, retain
useful artifacts, and name the exact remaining limitation and attempted recovery.
Do not use a generic evaluation-limitations status as a substitute for doing work.
Do not force edits if executed evidence shows no useful candidate.

## Completion

Report applied changes, paired outcomes and sample sizes, regressions, checks that
failed or did not run, remaining uncertainty, and exact rollback locations. Use
`APPLIED`, `PARTIALLY APPLIED`, or `NO CHANGE JUSTIFIED` for the change disposition;
report execution errors separately. For broad work, include mechanism coverage,
remaining deferred families and why analysis did or did not yield more changes.
A no-change result needs the actual evaluated
candidates and reasons, not merely the absence of a pre-existing benchmark.
Report a controller `no_change_evidence_insufficient` as "evidence insufficient", never as
"the harness is fine", and relay its ranked `next_steps` with their commands. Say the harness is
supported only for `no_change_harness_supported`, and only for the examined behaviors.

Rollback only this task's edits. Before restoration compare current hashes against
what this task wrote; preserve intervening user changes rather than overwriting an
entire file. Keep sanitized evaluation artifacts outside active skill discovery.

## Objective diagnosis gate

Use the operational criteria in the evaluation skill's trace-diagnosis reference.
Separate an observed pattern, a supported diagnosis, and a validated improvement.
Every candidate needs event references, an exposure denominator when known,
competing explanations, a responsible component, and a falsifiable outcome check.
Missing capture is a finding about evidence, not agent behavior. Measure required
boundaries and completion before efficiency; neither fewer calls nor more skills
is success. Check instruction removal and consolidation as well as additions.
