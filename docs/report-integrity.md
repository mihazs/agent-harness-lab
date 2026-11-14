# Report integrity fields

Reference for the fields an `ahl improve` or `ahl skills invoke` receipt
(`workflow.json`, also printed on stdout) carries so a reader can check an improving
agent's report. Every field derives from digests, trial outcomes and a read-only Git
status. None involves model judgment, carries hint, candidate or fixture text, or
grants recommendation, adoption or publication authority. Both entry routes write the
same values.

## `provenance`

| Field | Meaning |
| --- | --- |
| `agent_supplied_hypothesis` | One `{source, sha256}` entry per hint passed into the run: `focus` for `--focus`, `from_audit` for `--from-audit`, `static_audit` for the static convention an `ahl improve --history` run stages by itself when the history has findings but no verified outcome. The digest is of the hint text; the text is never stored. Empty for an unseeded run. |
| `candidate.independent_corroboration` | Present only for a seeded run, always `false`. |

The proposer saw the hint, so its candidate cannot independently confirm it, whether
or not the candidate resembles it. This holds for the automatic `static_audit` seed too, which is
a convention check and never a diagnosis. A report must not call a match an independent
finding. An unseeded run records an empty list and makes no independence claim.

## `evidence_strength`

Written for every run that proposed a change. `label` is one of:

- `behavioral_proof`: every criterion below holds.
- `source_correction_without_behavioral_proof`: the comparison is valid and the candidate passed
  every trial, but at least one criterion is missing (named in `reasons`).
- `insufficient`: the comparison is void or unsupported: `grader_changed_after_trials`,
  `frozen_inputs_changed`, `evaluation_not_for_final_attempt`, `no_covered_evaluation`,
  `candidate_arm_not_all_passed` or `candidate_regression_observed`.

| Criterion in `criteria` | Rule |
| --- | --- |
| `baseline_exercised_target` | Every development-split baseline trial reached a valid passed or failed verdict. |
| `baseline_failures` | Development-split baseline trials graded `failed`, at least `minimum_baseline_failures` (2). |
| `paired_comparison_supports_repair` | A development task failed on the baseline and passed on the candidate, with the frozen inputs unchanged. |
| `regression_or_transfer_control_ran` | A regression-split (`adjacent_regression`) or heldout-split (`transfer`) task has a valid verdict on both arms. |

`controls_run` and `controls_not_run` name those two controls. `regression_statement` is
`no regression observed in the adjacent-regression control that ran`, `regression observed`, or
`regressions not tested`; a transfer control alone never supports a no-regression statement. When the
comparison is void (changed grader or frozen inputs, or a non-final attempt) no control counts as run, so
`controls_run` is empty and the statement is `regressions not tested`.
`adoption_support` is true only for `behavioral_proof`. An outcome that is not an explicit pass or fail
stays unknown and is never counted as either.
The default single-run comparison has one failing development trial per arm, so it reports
`source_correction_without_behavioral_proof` by design. Fixed-bank (`--experiment-request`) runs do not
return per-trial records to the receipt and report `insufficient` with `no_covered_evaluation`.

## Frozen grader

The controller fingerprints the acceptance boundary (grader commands, grader files, grader
bundles and grader self-test declarations) before the trials and again after them. A difference
stops the run with `status: blocked` and `reason: grader_changed_after_trials`, writes no change
plan, and sets `comparison_valid_for_adoption: false`. Fresh trials under the new grader are a new
comparison; never regrade the old trials.

## `pre_edit_check`

Written when a change was proposed or a contract repair is staged. `uncommitted_changes` is `clean`,
`touching` (with `uncommitted_entries`) or `not_checked` when Git cannot be queried. The check runs
`git status` without taking locks and never changes the tree. The controller never queries a forge, so
`open_pull_requests` is always `not_checked`; the reporting agent runs that check itself and reports its result or `not_checked`.

## Receipt-sourced numbers

A final report writes each number as `VALUE [receipt: dotted.path]`, copied from the named receipt
field with its denominator, or writes `unknown`. Rates may cite a fraction as a percentage. Lint a draft:

```sh
python3 -m agent_harness_lab.report_lint REPORT.md workflow.json
```

The command prints one line per number without a matching source and exits 1. It ignores code spans,
fenced blocks, `#N` references, versions, dates, digests and list ordinals. It cannot check that
a "prior sessions" denominator excludes the current session; the skill states that rule.

## Why these fields exist

An agent can pass its own hypothesis into a run and present the matching candidate as independent
confirmation, report behavioral support from trials that never exercised the target, claim "no
regressions" with no regression control, edit a grader after trials, or write numbers no receipt holds.
Each field makes one of those claims checkable. They describe what the controller observed; they are
not a certification, and a `behavioral_proof` label is still bounded by the synthetic or local
tasks that produced it.

After a terminal result, an instruction to continue authorizes running the same controller again with
more evidence, such as `--history` or a different `--focus`. A model trial outside the controller is
`outside_budget_unverified` and counts against the user's authorization for model calls.
