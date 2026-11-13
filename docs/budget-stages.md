# Stage budgets and early stops

Reference for how `ahl improve` (and `ahl skills invoke`, which enters the same
controller) splits its shared `--timeout` budget by stage, what each stage may do
when it runs long, and the receipt fields that say where the budget went. For the
manifest the construction agent writes see the
[evaluation manifest contract](evaluation-manifest-contract.md).

## Why a split exists

One shared budget used to be spent first come, first served. A slow construction
pass left too little for trials, a refinement rebuilt the whole evaluation, and a run
whose every trial failed the same way in both arms still continued into another
attempt. The run then ended `budget_exhausted` without a conclusion, and the receipt
did not say which stage had taken the time. The split makes each of these a typed,
early and explained stop.

## Stages

Each stage has an allotment, a fraction of `--timeout`. The construction fraction is
configurable; the other stages keep their proportions of what it leaves.

| Stage | Default share | What is charged to it | Enforcement |
| --- | --- | --- | --- |
| `preparation` | 10% | Everything outside the other three in the first attempt: analysis, candidate proposal, freezing | Reported against its allotment; the shared deadline applies |
| `construction` | 25% (`--construction-fraction`) | The evaluation-construction pass or passes and the controller grader self-tests | Hard time box per pass: `evaluation_construction_timeout` |
| `trials` | 40% | The paired baseline and candidate trials and their grading | Reported against its allotment; the shared deadline applies |
| `refinement` | 25% | The whole second attempt (proposal, fresh held-out construction, trials) | The attempt starts only when the remaining budget covers this allotment: otherwise `refinement_budget_unavailable` |

`--construction-fraction FRACTION` takes a number strictly between 0 and 1 and is
available on `ahl improve` and `ahl skills invoke`; `ahl.json` limits do not carry
it. Raising the whole budget is `--timeout SECONDS`, or `limits.seconds` in
`ahl.json` for the default.

The cooperative construction prompt states the pass time box and the seconds that
remain in the whole budget, so the agent can plan its work.

## Construction time box

A construction pass that ends after its box, or is stopped at it, ends the run as
`budget_exhausted` with the reason `evaluation_construction_timeout` and a
`contract_violation` naming the stage, file and the next step. The agent gets one
more pass only when the remaining budget covers another box plus the whole trials
allotment; that attempt is recorded in `evaluation_construction_repairs` (reason
`evaluation_construction_timeout`, outcome `repaired` or `requested`). A second
overrun ends the run. The time box and the refinement reuse apply to the cooperative
(Codex and Claude) construction path; controlled Responses construction is only
accounted in the `construction` stage.

## Feasibility check

Before any trial is charged, the controller grades each task's `complete` control
through the same grader invocation the trials use and requires it to finish inside
the per-trial timeout. That timeout is the lower of 180 seconds (or the declared
`timeout_seconds`) and an equal share of the remaining budget for every planned
trial. A task that cannot meet it ends the run as `invalid_input` with the reason
`evaluation_task_infeasible` and the task position as the field; nothing is run.

## Uninformative evaluations

After the first trials, if no trial of either arm passed and both arms ended every
task with the same outcome, the run stops with the status `inconclusive` and the
reason `evaluation_uninformative`. No second construction or refinement follows. The
block `evaluation_uninformative` classifies each trial from execution facts only
(timeouts, exit codes, trial states and tool-error records in the native `trace.jsonl`),
never from grader output text:

| Class | Fact |
| --- | --- |
| `timeout` | The trial run hit the per-trial timeout |
| `runtime_failure` | The agent process failed to start, exited non-zero or its trace is incomplete |
| `grader_failure` | The grader invocation errored or timed out |
| `tool_failure` | The run completed and graded failed, but its trace records tool errors |
| `agent_failed` | The run completed, its native trace was read in full with no tool-error record, and the grader failed it |
| `unclassified` | The run completed and the grader failed it, but no native trace was read in full, so tool errors are neither seen nor ruled out |

Only a native `trace.jsonl` of at most 16 MiB is read. Offline and controlled
(Responses) trials write `stdout.txt` without native error records, and a trace that is
missing, unreadable or larger than the bound is not read at all, since a partial read
could miss later errors. Such trials are `unclassified`, never `agent_failed`, and
their `next_step` does not say whether a narrower `--focus` or a larger `--timeout`
would help. The stop itself still applies: no trial passed and both arms ended alike.

The block carries the class counts, the number of trials and tasks and one exact
`next_step`. Trials that never ran, ran out of budget or were rejected as invalid
are not outcomes and never trigger this stop.

## Refinement reuse

A refinement attempt keeps the frozen development and regression tasks, their
grader bundles and controls. The construction agent is asked for fresh held-out tasks
only. After it finishes, the controller rejects any task that is not `heldout`, copies
the frozen parts in and checks every copied byte against the digest taken when they
were frozen. Everything is then validated and frozen again before any trial, so the
freeze-before-trial rule, the frozen graders and the fresh held-out rule (an input
that an earlier attempt used is rejected) are unchanged. The receipt records
`evaluation_reuse` with `reused_tasks`, `fresh_heldout_tasks` and
`frozen_digests_verified`. Controlled (Responses) construction still rebuilds the
whole evaluation on refinement.

## Receipt fields

Every terminal receipt, including blocked, invalid and exhausted ones, carries
`budget_stages`: `profile`, `total_seconds`, `remaining_seconds`,
`construction_fraction`, per stage `allotted_seconds` and `spent_seconds`, and the
options that raise the budget (`raise_budget_option`, `raise_construction_option`).
Values are counts of seconds, never content.

A `budget_exhausted` receipt also carries `budget_exhaustion`:

| Field | Meaning |
| --- | --- |
| `evidence_label` | `insufficient`: nothing was concluded |
| `consumed_most_by` | The stage with the most spent seconds, or `none` |
| `observed` | Trials recorded, baseline failed and passed counts, trials unfinished; `unknown` when the evaluation never reported its trials (it raised, or ran through the fixed-experiment path) |
| `controls_not_run` | The adjacent regression and transfer controls that did not run; `unknown` under the same condition |
| `conclusion` | Always `not_established` |

## Recover from a budget stop

1. Read `reason` and `budget_stages` in `workflow.json`: the stage with the largest
   `spent_seconds` is where the budget went.
2. For `evaluation_construction_timeout`, raise `--timeout`, raise
   `--construction-fraction`, or narrow the run with `--focus`, then rerun.
3. For `evaluation_uninformative`, follow `evaluation_uninformative.next_step`; more
   budget alone does not help when both arms fail alike.
4. For `refinement_budget_unavailable` or any other `budget_exhausted` reason, raise
   `--timeout` and rerun.
