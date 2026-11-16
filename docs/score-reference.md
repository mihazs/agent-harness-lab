# `ahl score` reference

`ahl score` is the read-only per-area report described in
[score a harness per capability area](score-by-capability-area.md). It reads files and
prints to stdout; it calls no model, opens no network connection and writes nothing.

## Command

```text
ahl score [PROJECT] --spec FILE --records FILE [--area ID ...] [--format json|md]
```

| Input | Meaning |
| --- | --- |
| `PROJECT` | Directory holding `ahl.json` (default: the current directory). Its `areas` declare the areas; its `bank` names the bank, and the bank's digest must match `bank.sha256`. |
| `--spec FILE` | The JSON `ExperimentSpec` of the recorded paired experiment. |
| `--records FILE` | The JSON array of `TrialRecord` rows recorded for it, at most 16 MiB. |
| `--area ID` | Report only this declared area; repeatable. Rows keep the declared order, not the argument order. |
| `--format` | `json` (default) or `md`; the output is deterministic for the same inputs. |

Exit code 0 means a report was printed, whatever the states are. Exit code 2 means invalid
input: an unreadable, malformed or mismatched file, a bank digest that differs from
`ahl.json`, a bank task tagged with an undeclared area, or an `--area` that is undeclared
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
| `multiplicity` | `null`, or, when `--area` narrows the report to fewer areas than are declared, `areas_examined` (every declared area), `correction` (`none`) and a `note`; Markdown prints the note. |

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
