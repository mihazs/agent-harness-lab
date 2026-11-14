# Vacuity labels and result traceability

Reference for the five labels that mark an evaluation as proving nothing, where each
label is produced, what it blocks, and which receipt fields were audited so that every
reported number traces to a recorded trial.

A vacuous evaluation never produces `improved`, `supported`, a pass, a recommendation or
a decision that favors the candidate. A label states a recorded fact about the trials.
It is never a success, failure or independence claim, and an unknown capture or lineage
never becomes a label.

## The closed set

The five names are one closed set in `agent_harness_lab.evaluation.vacuity`
(`LABELS`, `LABEL_SET`). The improvement loop and every receipt reader import the set
from there; a name outside it is rejected by `vacuity.validate`.

| Label | Applies when | Derived from |
| --- | --- | --- |
| `no_eligible_cases` | No declared case has a usable identifier, so nothing could have been measured. | the manifest tasks |
| `all_outcomes_unknown` | Trials are recorded but none ended `passed` or `failed`: every outcome is an infrastructure, budget, input or grading unknown. | trial `status` |
| `arms_identical` | Baseline and candidate are the same frozen harness state (equal digests), so any difference between them is noise. An unknown digest never applies the label. | the two arm digests |
| `grader_not_executed` | Trials are recorded but none carries a recorded grader execution, so no verdict traces to a grader run. A grader that ran and errored is `all_outcomes_unknown`, not this label. | the trial `grader` record |
| `zero_trials` | No trial row is recorded. | the trial list |

Several labels can apply to one evaluation; they are reported in the order above.
`vacuity.classify(tasks, trials, baseline_sha256=..., candidate_sha256=...)` returns
them, `vacuity.receipt(labels)` builds the receipt block and `vacuity.classify_run(run)`
reads a recorded paired run.

## Where each surface applies them

| Surface | Behavior for a vacuous evaluation |
| --- | --- |
| `ahl improve` receipt | `status: inconclusive`, `reason: evaluation_vacuous` and a `vacuity` block with `labels`, `vacuous: true` and `improvement_allowed: false`. No recommendation or change plan is written. A receipt that already ends for another reason (`evaluation_uninformative`, `missing_or_duplicate_trials`) also carries the `vacuity` block. |
| `decision` block of the receipt | `disposition: invalid_evidence`, `observed_pattern: vacuous_evaluation`, the reason `vacuous_evaluation` and the same `vacuity` block. |
| `evidence_strength` block | `label: insufficient`, `adoption_support: false`, reasons `evaluation_vacuous` followed by the labels. |
| `ahl experiment` and every plan consumer | A plan whose baseline and candidate hashes are equal is rejected as `arms_identical` before any trial is scheduled or analyzed (exit 2). |
| `ahl compare` and the side-by-side report | An experiment whose arm hashes are equal marks every pair `not_comparable` with the reason `arms_identical`; no pair is `improved`. |
| Trial coverage (`legacy_coverage`) | A manifest with no declared task is never covered, even by zero trials. |

## Audit of reported numbers

Every field that reports a score, count or verdict was traced to its source. A field
either derives from recorded trial rows (or declared manifest rows) or is absent.

| Field | Source | With nothing recorded |
| --- | --- | --- |
| `status`, `reason` of a candidate or `no_change` verdict | exact trial coverage of the manifest, then trial statuses | typed incomplete, inconclusive or `evaluation_vacuous`; never a pass |
| `evaluation_uninformative.trials`, `tasks`, `class_counts` | recorded trial rows and their execution facts | block absent |
| `budget_exhaustion.observed` | recorded trial rows | the string `unknown`, not zero |
| `decision.observed` (`tasks`, `trials`, `pairs`, `by_split`) | manifest and recorded trial rows | the decision is not produced without exact coverage |
| `evidence_strength.criteria` counts | recorded baseline development rows | a count over zero rows reads zero and the label is `insufficient` with `no_covered_evaluation` |
| `spend_label` seconds | the stage clock, not a trial | not a result |
| `results.json` trial records | one row per executed trial; no aggregate score | empty list, never a summary |
| experiment `status: supported` | the exact planned trial set with executable-grader provenance | `incomplete`, never `supported` |
| side-by-side pair verdicts | one valid graded record per arm | `unknown` or `not_comparable` |
| history report pair `improved` | executable-grader provenance and equal runtime and grader identity | `unknown` or `not comparable` |
| workflow `outcome` `improved` | graded baseline `failed` and candidate `passed` in separate trial workspaces | `recommendation_only` or `inconclusive` |

Limits. The labels read recorded rows; they do not prove a grader is correct or that
trial rows were not forged, and `grader_not_executed` is defined over the evaluator's
trial rows (the `grader` execution record), not over hand-written experiment records,
whose provenance is a claim the analysis marks unverified. A non-vacuous evaluation is
still descriptive: the [evaluation foundation](evaluation-foundation.md) states what
certification requires.
