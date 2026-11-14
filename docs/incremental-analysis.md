# Reuse analysis work on unchanged input

[Command overview](command-reference.md) · [Private memory store](memory-store.md) · [Stage reuse planner](stage-reuse-planner.md) · [Analysis methods](analysis.md)

`ahl analyze --incremental` re-runs an analysis on an unchanged capture and project
harness without recomputing the one stage it can reuse today, the process-mining graph,
and says exactly why it reused or recomputed it. It is offline, makes no model call and
changes nothing outside the memory store you name. Without the flags `ahl analyze`
behaves exactly as before.

## Before you start

You need a private store created outside every checkout (see [create and check a
store](memory-store.md#create-and-check-a-store)) and an explicit capture file.

```sh
ahl memory init --store /abs/path/ahl-memory --exclude /abs/path/project
```

`--incremental` requires `--memory-store` and an explicit `--events` capture. A store
never turns on discovery, source scanning or retrieval, so incremental analysis of the
home-directory sessions, `--source-root`, `--memory-manifest` or a pinned native profile is
refused (exit 2) rather than guessed at. Isolated sandbox roles are refused as well.

## Run it twice

```sh
ahl analyze /abs/path/project --events /abs/path/events.jsonl --format normalized \
  --memory-store /abs/path/ahl-memory --incremental --explain-reuse
```

The first run computes the stage and commits its output and a record under an exact key.
The second run, with the same capture bytes and the same harness files, reuses it.
`analysis.json` and `report.md` are byte-identical to a run without the flags. The JSON
receipt gains a `reuse` object:

| Field | Meaning |
| --- | --- |
| `outcome`, `reason` | `reused` with `hit_verified`, or `executed` with the typed miss or invalid reason |
| `counts`, `reused_fraction` | Reused and executed deterministic stages; the fraction comes from stage counts, never trials, and is `null` when no stage was decided |
| `commit` | `committed`, `not_needed` (a hit), or why nothing was written: `refused`, `withheld` or `failed` with the store's reason. A failed write never invalidates the analysis |
| `counters` | Kept apart: `local_deterministic_cache` (avoided and executed stages, this run's collection time), `provider_cache` (`not_applicable`), `historical_reuse` (`reused_live_trials: 0`) and `tokens` (`model_calls: 0`; billing savings stay `unknown`) |

`--explain-reuse` adds the full `ahl-stage-reuse-v1` receipt (reused, executed, excluded
and unknown stages with reasons, and the historical stages that are never reusable), the
node key, the two input digests and, after a miss on an earlier run, `compared_to`, which
says whether the capture or the harness differs from the newest prior run.

## What forces a recompute

| Change | Result |
| --- | --- |
| Any byte of the capture | `miss_input_changed`; other cached runs stay valid, so restoring the capture hits again |
| Any harness instruction or skill file (content or mode), or a file added or removed | A new harness identity, `miss_input_changed`; nothing from the old identity is reused |
| `--format` or `--source-version` | `miss_algorithm_changed` |
| The project at another path | `miss_no_prior`: records are kept per project path |
| A release that changes the stage version | `miss_algorithm_changed` |
| No usable prior record, or only an interrupted one | `miss_no_prior` |
| A stored output that fails its hash or its canonical form | `invalid_integrity`; the stage is recomputed and nothing is written until you run `ahl memory verify` and `reindex` |

The harness identity covers the project's instruction files and skill bundles. A symlinked
harness file is refused with exit 2, because its target cannot be pinned.

## Failure behavior

- The capture is hashed before the run and again while it is read. A capture that changes
  in between stops the run with exit 2 and commits nothing.
- A reused output is cross-checked against the events counted in the same run (events,
  cases, reported roots and per-activity counts). A disagreement stops the run with exit 2
  and the stored output is not trusted.
- An analysis that fails after the decision (for example `--max-events` exceeded) leaves
  an `incomplete` diagnostic record. It is never a prior and never counted as a complete run.
- A missing store, a store that overlaps the project, the capture or `--output`, or an
  unusable index stops the run with exit 2 before any output is written. A store opened
  read-only by an older release can still be read, so a hit works and `commit` reports `failed`.

## Limits

Only the process-mining stage is reused, and the rest of the analysis still reads every
event, so the saving is that stage's work, not the whole run; elapsed time saved is
reported as `unknown`. Reuse is keyed to the exact capture, so appending an event
recomputes the stage. Other stages join after parity is shown for this one.
