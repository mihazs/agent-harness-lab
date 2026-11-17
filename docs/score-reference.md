# `ahl score` reference

`ahl score` is the read-only per-area report described in
[score a harness per capability area](score-by-capability-area.md). It reads files and
prints to stdout; it calls no model, opens no network connection and writes nothing.

## Command

```text
ahl score [PROJECT] --spec FILE --records FILE [--area ID ...] [--sessions FILE] [--format json|md]
```

| Input | Meaning |
| --- | --- |
| `PROJECT` | Directory holding `ahl.json` (default: the current directory). Its `areas` declare the areas; its `bank` names the bank, and the bank's digest must match `bank.sha256`. |
| `--spec FILE` | The JSON `ExperimentSpec` of the recorded paired experiment. |
| `--records FILE` | The JSON array of `TrialRecord` rows recorded for it, at most 16 MiB. |
| `--area ID` | Report only this declared area; repeatable. Rows keep the declared order, not the argument order. |
| `--sessions FILE` | Optional JSON array of content-free session signal summaries, at most 16 MiB; adds the [advisory session section](#advisory-session-placement). |
| `--format` | `json` (default) or `md`; the output is deterministic for the same inputs. |

Exit code 0 means a report was printed, whatever the states are. Exit code 2 means invalid
input: an unreadable, malformed or mismatched file, a bank digest that differs from
`ahl.json`, a bank task tagged with an undeclared area, a `--sessions` file that is not valid
(see [session summaries](#session-summaries)), or an `--area` that is undeclared
(`score_area_undeclared`) or repeated (`area_duplicate`). The two `--area` errors are typed
manifest-contract reasons with stage `score_selection` and a field such as `--area[1]`.

## How the numbers are computed

No new statistic is defined. For each area the plan is restricted to the experiment tasks
tagged with it, the family weights are renormalized, and the trials of those tasks are
analyzed with `agent_harness_lab.evaluation.statistics.analyze`; the success rate of an arm
is the loop's `absolute_score` (the mean of per-root mean outcomes with the
`statistics.bound` interval at the plan's `alpha`). A repetition is a unit of its root. A
trial counts only when its record is graded by an executable grader with a boolean outcome;
a missing record, an ungraded one and a trial id recorded twice are unknown, and an unknown
outcome is never a pass, a fail or an independence label. A record the plan did not schedule
is ignored. Areas are not corrected against each other, and a task in several areas is
analyzed in each.

## Output

| Field | Meaning |
| --- | --- |
| `profile` | `ahl-score-v1`. |
| `experiment` | `id`, `profile`, `partition`, `alpha`, `min_roots`, `repeats` of the plan. |
| `arms` | `["baseline", "candidate"]`, or `["baseline"]` when no candidate trial was recorded. |
| `trials` | `planned` trials of the plan and `recorded` usable ones. |
| `untagged_tasks` | Experiment tasks that carry no area (or are not in the bank); they count in no area. |
| `areas[]` | One row per declared (or selected) area, below. |
| `overall` | The same row shape over every experiment task, or `null`. |
| `overall_withheld` | `null` when `overall` is shown, else `not_every_area_scored`, `area_selection` or `no_declared_area`. |

An area row has `id`, `label`, `bank_tasks` (tasks of the bank with the tag), `tasks` and
`independent_roots` (tasks and distinct roots of the experiment with the tag), `state`,
`reason`, `success`, `paired` and `critical_regressions`. `success` holds one entry per
recorded arm with `success_rate`, `lower`, `upper`, `independent_roots`, `unknown_roots` and
`evidence`; an arm with no known outcome is `null`. `paired` holds the fixed analysis
`status` and `reason`, and, when it computed an interval, `delta`, `lower`, `upper` and
`independent_roots`; it is `null` without a candidate. `critical_regressions` counts the
graded records flagged as a critical regression. A `blocked` or `no_evidence` row has `null`
for `success`, `paired` and `critical_regressions`.

## States and reasons

<a id="reasons"></a>

| State | Reason | Cause |
| --- | --- | --- |
| `scored` | none | Every outcome known, at least `min_roots` roots, every task reviewed. |
| `provisional` | `unknown_outcomes` | A root has a missing, ungraded or repeated trial. |
| `provisional` | `below_minimum_roots` | Known outcomes, but fewer roots than `min_roots`. |
| `no_evidence` | `no_experiment_task` | No experiment task carries the area. |
| `no_evidence` | `no_graded_outcome` | No outcome of the area is graded, so nothing was measured. |
| `blocked` | `bank_not_qualified` | A task of the area has a bank inspection issue (unreviewed, quarantined, overlapping), or the bank has an issue that names no task. |
| `blocked` | `experiment_design_unsupported` | The plan is sequential or carries a cohort manifest; per-area analysis covers the fixed plan only. |
| `blocked` | `provenance_mismatch`, `root_similarity_binding_missing_or_invalid` or another reason | The fixed analysis returned status `blocked` for the area (records from another grader or runtime, root similarity evidence the plan pins but `ahl score` does not take, identical arms); its own reason is shown. |

The Markdown form prints the same table with the same words and `n/a` for a missing number.

`scored` rests on the bank's own review declarations (`independence`, `review_status` and
`permission` of each task) and the plan's minimum of roots; `ahl score` does not verify the
declarations, and the report is a recommendation-only view, never certification or adoption.

## Advisory session placement

With `--sessions`, the report gains an `advisory_sessions` key (and, in Markdown, an
"Advisory: recorded sessions" section below the scored table). Without the option the key is
absent and the report is unchanged. The placement is deterministic and uses no model, network
or file other than the summaries. It is advisory and observed-only: a session is not a
controlled task, so it never produces a `scored` state, never changes a row or the overall
figure, and never counts as evidence for an area's state. An area can show sessions here and
still be `no_evidence` above.

### Session summaries

`--sessions` reads a JSON array of at most 5,000 objects. Each has a required `session` (an
opaque id of 1 to 64 characters from `A-Z a-z 0-9 . _ : -`, unique in the file) and optional
`tools`, `extensions` and `commands` maps of at most 64 names to integer counts from 1 to
1,000,000. Tool names use `A-Z a-z 0-9 _ . : -` (up to 64 characters), extensions are
lower-case without the dot (up to 16 characters) and command families are lower-case
program names (up to 32 characters), with `git` and `gh` written as `git-diff` or `gh-pr`. A
summary holds counts only: never a path, a command line, an argument or text. Any other field,
a repeated id or a bad name or count exits 2 with a message that names the position, such as
`sessions[3].extensions`, and never the value. A name the rules below do not know is accepted
and carries no signal.

### Rules

Each recognized extension or command family is one signal for the area in the table. The
rules know the four built-in suggestions only (see
[declare capability areas](declare-capability-areas.md)); a project-specific area has no
rule, so its row shows `sessions: null` and `classifiable: false`, never a zero.

| Area | Extensions | Command families |
| --- | --- | --- |
| `coding` | `py js mjs cjs ts go rs java kt c h cc cpp hpp cs rb php swift sql sh` | `pytest python node npm pnpm yarn cargo go make mvn gradle dotnet tsc ruff mypy uv` |
| `ux-ui` | `css scss sass less html htm vue svelte jsx tsx svg` | `playwright cypress storybook stylelint lighthouse` |
| `docs` | `md mdx rst adoc txt` | `markdownlint vale mkdocs sphinx-build pandoc` |
| `review` | none | `git-diff git-log git-show git-blame gh-pr` |

Review commands count only for a session whose `tools` contain none of the file-changing tools
`Edit`, `MultiEdit`, `Write`, `NotebookEdit` or `apply_patch`, because reading diffs is routine
inside changing work. A session is placed in an area only when it has at least 3 recognized
signals and the leading area holds at least three fifths of them. Otherwise it is
`unclassified` with one reason, and it is never given the nearest area:

| Reason | Cause |
| --- | --- |
| `no_signal` | No recognized signal: an empty summary, or only names the rules do not know. |
| `below_minimum_signal` | One or two recognized signals. |
| `ambiguous` | The leading area holds less than three fifths of the recognized signals. |
| `area_not_declared` | The session looks like a built-in area that `ahl.json` does not declare. |

### Fields

| Field | Meaning |
| --- | --- |
| `profile` | `ahl-session-areas-v1`. |
| `label`, `evidence` | Always `advisory` and `observed_only`. |
| `recorded` | Sessions read. |
| `areas[]` | One entry per declared (or `--area` selected) area: `id`, `label`, `state` (`advisory`), `classifiable` and `sessions` (`null` when no rule exists). |
| `unclassified` | `state` (`unclassified`), `sessions`, and `reasons` with a count for each reason above. |
| `not_selected` | Sessions placed in a declared area that `--area` left out; the placement itself does not depend on `--area`. |
| `rows[]` | One entry per session in file order: `session`, `state` (`advisory` or `unclassified`), `area` (`null` when unclassified), `reason` and `signals` (recognized signal counts per area). |
