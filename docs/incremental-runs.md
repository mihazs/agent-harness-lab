# Incremental runs

Reference for what a second `ahl improve` on the same project does with the first. The run reads
the previous run's record and receipt, classifies what happened to the earlier candidate from the
harness files, adds an `incremental` block to `workflow.json` (also printed on stdout), and stops
one finished comparison from being repeated. A first run has no block.

Everything here is an observed pattern. The block never grants recommendation, adoption,
promotion or publication authority: `adoption_authority`, `automatic_promotion` and
`publication_allowed` are always `false`, and the block is private like every receipt.

## What is read, and from where

| Source | Used for |
| --- | --- |
| `harness-states.json` in each run folder | The harness files the run saw (path, SHA-256, mode) and the candidate it froze, if any. Written by `ahl improve` into the private run store only. |
| `latest-run.json` beside the run folders | Which run is the previous one for this project. |
| The previous `workflow.json` | The previous outcome, its `before_after` block and its `investigation.cause`. |
| The project's harness files now | Whether the earlier candidate was applied, ignored or edited. |

A run started with `--output` outside the private run store still reads the previous run but is
not recorded, so a later run cannot find it. A record or pointer that is unreadable, oversized,
malformed, symlinked or names a missing run is `unavailable` with reason `previous_run_unreadable`:
it is never read as a first run, and the run itself continues unchanged. An unreadable previous
receipt leaves the earlier outcome `unknown` while the harness comparison still runs.

## Dispositions

Each path the earlier candidate changed is compared with the earlier baseline and candidate by
bytes and mode. The disposition says what happened to it. It is read from file contents, so it does
not prove that `ahl apply` was used.

| `disposition` | Meaning |
| --- | --- |
| `applied` | Every path the candidate changed now holds the candidate's bytes and mode (a removed file is absent). |
| `ignored` | Every such path still holds the earlier baseline. |
| `partially_applied` | Some paths hold the candidate and some the baseline. |
| `edited` | A path the candidate changed matches neither arm. |
| `no_candidate` | The previous run froze no candidate, so there was nothing to apply. |
| `unknown` | The previous run used another harness scope (`reason: scope_changed`); nothing is compared. |

`harness` lists, with exact counts and at most 20 names each, the `applied`, `ignored`, `edited`,
`added` and `removed` paths, and whether the harness changed since the previous baseline. A path the
candidate did not touch that now differs from the baseline is `edited` and raises the flag
`harness_edited_outside_candidate` beside an applied, ignored or partially applied disposition.

`previous.candidate_regressed` is read from the previous receipt and never inferred: true only when
that run ended `candidate_regressed` for the very candidate its record froze. It is `null` when the
receipt or the candidate is unknown. A regressed candidate that was nevertheless applied raises
`regressed_candidate_applied`.

## Historical and fresh scores

`historical` is the earlier paired evaluation from the previous receipt, labelled
`historical_observation`. `fresh` is this run's own evaluation, labelled `fresh_evaluation`, and
exists only when this run evaluated. Earlier trials are never reused as fresh execution: the
fresh scores always come from this run's own trials. The two are subtracted
(`fresh_baseline_minus_historical_candidate`, `fresh_baseline_minus_historical_baseline`) only when
`fresh.comparison.status` is `identical`, that is, every frozen input (bank, grader, seed schedule,
budget share, runtime) is known and equal on both. A different or unrecorded input makes the status
`mismatch` or `unverified` and no difference is shown. Each run authors its own bank unless the
project pins one, so expect `mismatch` for an unpinned bank.

## Stages

`stages` names each stage and whether this run reran or skipped it, with the reason.

| Stage | Action | Why |
| --- | --- | --- |
| `evidence_collection` | `rerun` | No verified earlier output is reused here; reuse of a deterministic analysis stage is opt-in under `ahl analyze --incremental`. |
| `candidate_preparation` | `rerun` | A model stage is never replayed. |
| `evaluation_construction` | `rerun`, or `skipped` | Held-out inputs are always fresh; skipped only for the repeated regression below. |
| `trials` | `rerun`, or `skipped` | Earlier trials are historical only; skipped only for the repeated regression below. |

## A finished regression is not repeated

When preparation freezes the same candidate on the same baseline, harness scope and runtime that
the previous run measured worse for a cause the candidate owns (`investigation.cause` of
`candidate_caused`), the run ends before constructing any evaluation:

| Field | Value |
| --- | --- |
| `status`, exit | `blocked`, 3 |
| `reason` | `candidate_already_regressed` |
| `previous_run` | The run folder name holding the measurement. |
| Skipped | `evaluation_construction` and `trials`; `incremental.stages` says so. |

A regression owned by the pipeline or undetermined, a different candidate, a changed baseline,
scope or runtime, or an unknown runtime is evaluated as usual. To get a different candidate, change
the focus or the harness, or read `investigation.json` of the earlier run first. Preparation has
already run when the guard fires, so the result also carries `spend_label: spent_without_evaluation`.

## Not covered here

The loop's round ledger, resume semantics and carry-over of the best snapshot are owned by the
[loop reference](loop-reference.md) and consume this detection. The fixed repeated-bank lane records
the baseline and candidate states but has no earlier `candidate_regressed` receipt to repeat.
Related: [before/after guard](before-after-guard.md), [typed exit receipts](typed-exit-receipts.md),
[stage reuse planner](stage-reuse-planner.md).
