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

The run store is keyed by the project path you pass, so two runs in different temporary worktrees never see each other. The
[isolated project check](isolated-project-check.md) gives checks of one repository that run in turn the same worktree path;
share `AHL_STATE_HOME` with them to make a second check find the first.

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

`previous.candidate_regressed` is read from what the previous run measured and never inferred. It is
`true` for a regression measured on the very candidate the previous record froze, for a cause the
candidate owns, or one carried forward from an earlier run (see below). It is `false` only when a
paired evaluation with a non-regressed verdict is bound to that frozen candidate's attempt. It is
`null` when the candidate was never measured or the measurement is not about it: an interrupted or
unreadable receipt, a run that stopped before any trial, a guard block that is `unavailable` or has no
paired outcomes, another attempt, a regression owned by the pipeline or undetermined, or no frozen
candidate. A regressed candidate that was nevertheless applied raises `regressed_candidate_applied`.

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
| `evidence_collection` | `reused`, or `rerun` | `reused` with `hit_verified` only when `ahl improve --incremental` found a verified stored output for this exact capture and harness (see [reuse the evidence stage](#reuse-the-evidence-stage)). Otherwise `rerun`: with the planner's miss or invalid reason when reuse was requested, and `no_verified_prior_output` when it was not. |
| `candidate_preparation` | `rerun` | A model stage is never replayed. |
| `evaluation_construction` | `rerun`, or `skipped` | Held-out inputs are always fresh; skipped only for the repeated regression below. |
| `trials` | `rerun`, or `skipped` | Earlier trials are historical only; skipped only for the repeated regression below. |

## Reuse the evidence stage

The one stage with a deterministic output is the process-mining part of evidence collection.
`ahl improve` reuses it under the same opt-in as [`ahl analyze --incremental`](incremental-analysis.md):

```sh
ahl improve /abs/path/project --events /abs/path/events.jsonl --format normalized \
  --memory-store /abs/path/ahl-memory --incremental --explain-reuse
```

`--incremental` needs `--memory-store` and an explicit `--events` capture, and is refused (exit 2,
before any run folder exists) with `--history`, a pinned native profile or an isolated role. The
receipt gains the same `reuse` object as `analyze`, and the stage row above says what happened.
The decision follows the exact bytes of the capture and the harness files, so each disposition
reuses a different thing:

| Earlier candidate | Harness now | `evidence_collection` |
| --- | --- | --- |
| `ignored`, or a repeated regression | The earlier baseline | `reused`, `hit_verified` |
| `applied`, `partially_applied` or `edited` | Different bytes or modes | `rerun`, `miss_input_changed` |
| Any | The capture changed | `rerun`, `miss_input_changed` |

A reuse hit changes nothing else: preparation, evaluation construction and trials are never
reused, and `analysis.json` and `report.md` match a run without the flags. The `reuse` object and
the `incremental` block follow the store and the call order, so receipt parity between the CLI and
skill routes ignores both.

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

The measurement is copied into the record of every later run (`regressions` in `harness-states.json`),
so a repeat is found however many blocked repeats, runs that proposed nothing, interrupted runs or
other candidates came in between. The newest 50 regressions are kept; an older one is forgotten and
its candidate is evaluated again. A previous record that is unreadable ends the memory: that run
reports `unavailable` and carries nothing forward. A blocked repeat reports `previous_run` as the run
that measured, not the run just before it. When the previous receipt holds no measurement of its own,
`incremental.historical` is the carried measurement and names its `run`.

A regression owned by the pipeline or undetermined, a different candidate, a changed baseline,
scope or runtime, or an unknown runtime is evaluated as usual. To get a different candidate, change
the focus or the harness, or read `investigation.json` of the earlier run first. Preparation has
already run when the guard fires, so the result also carries `spend_label: spent_without_evaluation`.

## Loop runs

A loop run (`--accept-score`, `--max-rounds` or a `loop` declaration) reads the same previous record
and receipt, then adds a `carry_over` block to its `loop` block. The detection above is consumed as it
is, not repeated: a harness edited outside the earlier candidate reopens the carried mechanism
dispositions, and different frozen inputs make the earlier scores `not_comparable` with no delta. The
earlier score stays a labelled historical observation beside the fresh baseline evaluation and is never
reused as a trial. See [later runs](loop-reference.md#later-runs) for the block and
[resuming a loop](loop-reference.md#resuming-a-loop) for an interrupted run.

## Not covered here

Frozen development and regression tasks are never reused across runs: held-out inputs stay fresh
whatever an earlier run did. The loop's round ledger, resume semantics and carry-over of the best snapshot are owned by the
[loop reference](loop-reference.md) and consume this detection. The fixed repeated-bank lane records
the baseline and candidate states but has no earlier `candidate_regressed` receipt to repeat.
Related: [before/after guard](before-after-guard.md), [typed exit receipts](typed-exit-receipts.md),
[stage reuse planner](stage-reuse-planner.md).
