# Aim an improvement at chosen areas and gate adoption per area

Use this when a harness change might help one kind of work and hurt another, such as
coding and UX/UI, and a pooled score would hide the loss. `ahl improve` can run only the
bank tasks of the areas you name, and it never lets a candidate through when a scored area
loses paired outcomes, even when the pooled result did not fall. The fields are listed in the
[area gate reference](area-gate-reference.md).

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

## Read the gate in the receipt

The receipt of a project that declares areas carries an `area_gate` block next to
`before_after`. Read each area's `state` before its numbers:

| State | What it means for adoption |
| --- | --- |
| `scored` | Every pair of the area has a known outcome on both arms and the area has at least two roots. It passes, or it regresses when its paired delta is below the negative `--regression-margin`. |
| `provisional` | An outcome is unknown or there are fewer than two roots. The interval is shown, and the area neither passes nor regresses. |
| `no_evidence` | No pair carries the area, or none has a known outcome. Nothing is shown. |

The areas that were not measured are listed in `not_measured`; they never count as a pass.
When a scored area regressed, the run ends `blocked` with reason `area_regressed` (exit
code 3), carries no recommendation, and writes an `investigation.json` whose `per_area`
block lists each area's delta and flipped trials. The one step to take is in
`area_gate.remediation`: revise or discard the candidate so no named area loses outcomes,
then rerun. Do not raise the margin after seeing the result.

When more than one area was examined the block carries a `multiplicity` note with the number
of areas. No interval is corrected across areas, so a win in one area among several is
weaker evidence than the same win in an area you chose before the run.

## Use it with the loop

With a loop declaration (`--accept-score`, `--max-rounds` or a `loop` object) each paired
round is gated the same way over the development and regression tasks it scores. A candidate
that regressed an area stops the loop with the criterion `regression` even when its pooled
score meets the bar; the round records `regressed_areas`, and `area_gates` in the receipt
lists the gate of every paired evaluation.

To read a per-area result from a run that already finished, use
[`ahl score`](score-by-capability-area.md); with `--area` it adds the same multiplicity note.
