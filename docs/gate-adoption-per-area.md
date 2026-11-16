# Aim an improvement at chosen areas and gate adoption per area

Use this when a harness change might help one kind of work and hurt another, such as
coding and UX/UI, and a pooled score would hide the loss. `ahl improve` can run only the
bank tasks of the areas you name, and it never lets a candidate through when a scored area
loses paired outcomes, even when the pooled result did not fall. The default loop and the
deprecated `--single-pass` end with different typed outcomes, compared [below](#read-the-result-by-route);
the fields are listed in the [area gate reference](area-gate-reference.md).

## Before you start

You need a project `ahl.json` that pins a task bank, declares the areas, and a bank whose
tasks carry `areas` tags ([declare capability areas](declare-capability-areas.md)). Without
a pinned bank `--area` stops before any model call with `area_selection_requires_pinned_bank`.

Tag at least one development, one calibration and one held-out (certification or transfer)
task with each area you plan to select. A task may carry two areas, so a shared control task
can serve both.

## Aim a run at one or more areas

```sh
ahl improve . --area ux-ui
ahl improve . --area ux-ui --area coding
```

Only the bank tasks tagged with a selected area run, and the shared trial budget is sized to
that count (one preparation plus a pair for each selected task). The run is checked before it
spends anything:

- an undeclared id stops with `area_selection_undeclared`, and a repeated id with
  `area_duplicate`; both name the position (`--area[1]`), never the value;
- a selection that names no executable task stops with `area_selection_empty`;
- a selection that lacks a development, calibration or held-out task stops with
  `area_selection_requires_development_regression_and_heldout`.

Without `--area` every task runs and every declared area is gated.

## Read the result by route

`ahl improve` runs the evaluate, improve, evaluate loop whenever the project pins a bank, which
this page requires, so the first row below is the route you take unless you pass the deprecated
`--single-pass`. The two routes gate the same way and end with different typed outcomes:

| Route | Typed outcome when a scored area regressed | Exit | Where the gate is | The one step |
| --- | --- | --- | --- | --- |
| `ahl improve` (the default loop) | status `not_accepted`, reason `loop_regression`, `loop.stop_criterion` `regression` | 3 | an `area_gates` entry for each paired evaluation (with its `evaluation_id`), and `regressed_areas` in the `loop.rounds` entry of the stopped round | `loop.remediation`, repeated as `exit_receipt.next_step`; it names this gate |
| `ahl improve --single-pass` | status `blocked`, reason `area_regressed` | 3 | an `area_gate` block next to `before_after`, and `per_area` in `investigation.json` | `area_gate.remediation` |

`area_regressed` and `area_gate` exist only in a single-pass receipt; the loop never writes
them. A pooled regression that no area explains keeps its own reason on each route
(`loop_regression` with the generic remediation in the loop, `candidate_regressed` in a single
pass). Read each area's `state` before its numbers, in `area_gates[].areas[]` or `area_gate.areas[]`:

| State | What it means for adoption |
| --- | --- |
| `scored` | Every pair of the area has a known outcome on both arms and the area has at least two roots. It passes, or it regresses when its paired delta is below the negative `--regression-margin`. |
| `provisional` | An outcome is unknown or there are fewer than two roots. The interval is shown, and the area neither passes nor regresses. |
| `no_evidence` | No pair carries the area, or none has a known outcome. Nothing is shown. |

The areas that were not measured are listed in `not_measured`; they never count as a pass.
When a scored area regressed, neither route carries a recommendation and the harness on disk
is unchanged. Revise or discard the candidate so no named area loses outcomes, then rerun. Do
not raise the margin after seeing the result.

When more than one area was examined the block carries a `multiplicity` note with the number
of areas. No interval is corrected across areas, so a win in one area among several is
weaker evidence than the same win in an area you chose before the run.

## What the loop adds

In the loop each paired round is gated over the development and calibration tasks it scores;
the held-out tasks are never run, although the selection must still contain one. A candidate
that regressed an area stops the loop with the criterion `regression` even when its pooled
score meets the bar and whatever `--on-regression` says, so `rollback_continue` does not try
another mechanism after an area loss. The round records `regressed_areas`, and
`area_gates` lists the gate of every paired evaluation, including the ones that passed.

To read a per-area result from a run that already finished, use
[`ahl score`](score-by-capability-area.md); with `--area` it adds the same multiplicity note.
