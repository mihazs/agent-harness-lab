# Before/after guard

Reference for how `ahl improve` judges a candidate against its baseline on identical
frozen inputs, and for what it records when the candidate is worse. The same
`improve` run that executes both arms adds a `before_after` block to `workflow.json`
(also printed on stdout), in the default evaluation and in the
[fixed repeated-bank lane](#the-fixed-repeated-bank-lane). A candidate that regressed ends
`blocked` with reason `candidate_regressed` and an `investigation.json` beside the receipt,
instead of the bare `evaluation_failed` (default lane) or the `candidate_not_supported`
recommendation (fixed lane) it ended with before.

Everything here is an observed pattern. No verdict grants recommendation, adoption,
promotion or publication authority; `adoption_authority`, `automatic_promotion` and
`publication_allowed` are always `false`, and the block is private like every receipt.

## Verdicts

The paired delta is the mean over roots of the candidate score minus the baseline score,
where a root's score is its share of passed trials. A root is the task unless the manifest
declares one, so repeats inside a root never count as independent evidence.

| Verdict | Rule | Adoption |
| --- | --- | --- |
| `candidate_regressed` | The paired delta is below `-margin`. | Blocked. |
| `improved` | The delta interval lies above `margin`, no outcome is unknown and every frozen input is known and identical on both arms. | Not blocked, never granted. |
| `inconclusive` | Anything else: a gain inside noise, a tie, a loss inside the margin, an unknown outcome or an unverified frozen input. `reasons` names each cause. | Not blocked, never granted. |

`candidate_regressed` is decided on the observed delta alone, so a small bank cannot hide a
loss behind a wide interval. `improved` also needs the interval, so a gain inside noise is
`inconclusive`, never `improved`. `delta.interval_excludes_margin` says whether the interval
agrees with a regression. An outcome that is not an explicit pass or fail is unknown: it is
never counted as a failure, never makes a pair eligible, and withholds `improved`. With no
eligible pair the verdict is `inconclusive` with reason `no_paired_outcomes`.

The default `ahl improve` evaluation runs one trial per task and arm. With seven or fewer
roots even a clean sweep cannot lift the interval above a margin of `0.0`, so the guard reports
`inconclusive` for a helpful candidate and `candidate_regressed` for a harmful one. The existing
certification prerequisites are unchanged.

## Declare a margin

`ahl improve --regression-margin 0.1` tolerates a paired loss of up to `0.1` (a number from 0
up to but not including 1; the default `0` blocks any net loss). A candidate worse by more is
`candidate_regressed`; a loss equal to the margin is tolerated and stays `inconclusive`. The same
margin is the bar the delta interval must clear for `improved`, so a larger margin makes `improved`
harder, never easier. The value is recorded as `before_after.margin` and in `investigation.json`.
An out-of-range or non-numeric value is an input error (exit 2) before any run, and the option cannot be
combined with `--experiment-request`, whose request declares its own margins. A project `ahl.json` cannot
set it.

## The `before_after` block

| Field | Meaning |
| --- | --- |
| `profile` | `ahl-before-after-guard-v1` |
| `verdict`, `reasons` | One verdict above and the reasons that produced it. |
| `adoption_blocked` | True only for `candidate_regressed`. |
| `margin`, `alpha` | The tolerated loss (default `0.0`: any net loss) and the interval error level (`0.05`). |
| `gain_margin` | Present only when the bar for `improved` differs from `margin`, as in the fixed lane. |
| `pairs` | `eligible`, `unknown`, `flipped_to_fail`, `flipped_to_pass`, `unchanged` and `repeats_per_task_arm`. |
| `scores.baseline`, `scores.candidate` | Mean pass share with a conservative interval (Hoeffding over roots, as in the fixed analysis). |
| `delta` | `mean`, `low`, `high`, `method` and `interval_excludes_margin`; `null` without an eligible pair. |
| `frozen_inputs` | The identity each arm ran under and their comparison, below. |
| `bindings` | Attempt, final attempt, `describes_final_attempt`, and the evaluation, manifest and `results.json` identities. |

Only the final attempt's evaluation relabels the run. When a later attempt ended before
evaluating, the block still reports the earlier comparison with `describes_final_attempt: false`
and the run keeps its own status. If the guard cannot be computed the block is
`{"status": "unavailable"}` with the error type and the run result is unchanged.

## Frozen inputs identity

Both arms carry one identity: `bank_sha256`, `grader_sha256`, `seed_schedule_sha256`,
`budget_share`, `runtime_sha256` and the `identity_sha256` of those five. A field the
controller could not observe is `null`, which is unknown and never a match.
`bank_sha256` is the project bank digest, or for a constructed manifest the digest of its
task content without the run's own budget fields; `grader_sha256` is the grader boundary
digest (argv, files, bundles and self-tests). Both name the run folder by a placeholder, so
the same inputs have the same identity in another run. `seed_schedule_sha256` covers the
ordered task ids, repeats and declared seed, and `budget_share` is the number of trials each
arm ran; the per-trial timeout is derived from the remaining budget, so it is not part of
the identity, and both arms always run under the one value in the manifest.
`runtime_sha256` is the sandbox image identity for controlled runs and the launch
configuration both arms shared (agent, resolved executable, environment policy) for native
runs; it binds neither the served model nor the runtime build.

`frozen_inputs.comparison.status` is `identical`, `mismatch` (a field known on both arms
differs, named in `mismatched`) or `unverified` (a field is unknown, named in `unknown`).
Python callers can compare identities from different runs with
`agent_harness_lab.evaluation.before_after.compare_identities`.

## `investigation.json`

Written next to `workflow.json` only for `candidate_regressed`; the receipt carries
`investigation.path`, `investigation.sha256` and `investigation.cause`.

| Field | Meaning |
| --- | --- |
| `per_task` | Each pair with its split, areas, both outcomes (`passed`, `failed` or `unknown`) and its delta. |
| `per_area` | Pairs, mean delta and flips per declared area; a task without areas is `untagged` and no report reads that as coverage. |
| `flipped` | The pairs that went `to_fail` and `to_pass`. |
| `changes` | Whether the `grader` and the `frozen_inputs` changed, and whether the `budget` was identical, differed or is unknown. |
| `cause.class` | `candidate_caused`, `pipeline_caused` or `undetermined`. |
| `cause.basis`, `cause.caveats`, `next_step` | The facts behind the class, the limits that remain and one content-free next step. |

`pipeline_caused` means a grader, the frozen inputs, the bank, the seed schedule, the budget
or the runtime differed between the arms; only that class is an AHL defect and needs a
regression test. `candidate_caused` means every frozen input was known and identical, so the
harness change made the score worse; a single trial per arm still cannot separate that from
run-to-run variance, which the caveat says. `undetermined` means a fact was unknown, and an
unknown fact is never turned into either cause.

## Read a regressed run

1. Open `investigation.json` from the run output named in the receipt.
2. Read `cause.class`. For `candidate_caused`, read `flipped.to_fail`, then revise or discard
   the candidate. For `pipeline_caused`, read `comparison` and `changes`, fix the difference,
   add a regression test for it and rerun. For `undetermined`, establish the unknown fact first.
3. Rerun `ahl improve` once the cause is addressed.

## The fixed repeated-bank lane

A sealed `--experiment-request` comparison carries the same `before_after` block, built from the
controller's recorded graded trials after the decision receipt is verified. The request supplies
what the default lane takes from the command line: the tolerated loss is its `family_margin`, the bar
for `improved` its `effect_margin` (recorded as `gain_margin`) and the interval error level its `alpha`.
Each trial repetition of a task is one pair, a root's repeats weigh the root once, and the declared
family is the area in `investigation.json`. Each arm's identity is read from its own records: a missing
trial, or records that disagree on the grader or runtime, make the comparison `unverified` or a named
`mismatch` instead of a match, and `bank_sha256` is the request's bank digest. `bindings` carries the
`controller_run_id` and `describes_final_attempt: true`; the fixed lane runs one attempt.

When the observed paired delta is below `-family_margin` the run ends `blocked` with reason
`candidate_regressed` (exit 3) and carries no `recommendation`; the fixed analysis, the decision receipt and
the controller run id stay on the receipt unchanged. Otherwise the status, reason and recommendation are
exactly what the fixed analysis produced, and the block only adds the observed pattern beside them. If the
guard cannot be built the block is `{"status": "unavailable"}` and the run result is unchanged. The guard
weighs roots equally and does not merge roots the request marks as similar, so it never replaces the
fixed analysis, which keeps its family weights and root grouping.

## Limits

- Only a sealed comparison with a current, verified decision carries the block; a run that ended
  earlier (incomplete, quarantined, quota or permission stop) keeps its own status.
- Independence of roots stays unverified, intervals are conservative and not a power guarantee,
  and nothing here certifies a candidate. See [report integrity](report-integrity.md) for the
  evidence-strength label and [decision receipts](decision-receipts.md#default-improve-single-run-dispositions)
  for the single-run disposition this block sits beside.
