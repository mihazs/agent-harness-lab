# Area gate reference

The per-area adoption gate and the `--area` selection of `ahl improve`. The task-oriented
steps are in [aim an improvement at chosen areas](gate-adoption-per-area.md).

## `--area`

```text
ahl improve [PROJECT] --area ID[,ID...] [--area ID ...]
```

| Rule | Detail |
| --- | --- |
| Needs | A project `ahl.json` that declares `areas` and pins a bank (`bank.path` other than `starter`); otherwise `area_selection_requires_pinned_bank`. |
| Selection | The executable bank tasks tagged with any selected area, in bank order. A quarantined task is never selected. |
| Budget | The shared trial ceiling must cover `1 + 2 * (selected tasks)`; the exported manifest allows `2 * (selected tasks)` trials. |
| Partitions | The selection must hold a development (or construction), a calibration and a certification (or transfer) task. |
| Checked | Before any model call, so a bad selection costs nothing. |

Typed reasons, all project declaration defects that name a position and never a value:

| Reason | Cause |
| --- | --- |
| `area_selection_undeclared` | An `--area` id that `ahl.json` does not declare. |
| `area_duplicate` | An `--area` id given twice. |
| `area_selection_empty` | No executable, non-quarantined task carries a selected area. |
| `area_selection_requires_pinned_bank` | `--area` without a pinned bank. |
| `area_selection_requires_development_regression_and_heldout` | The selected tasks lack a development, calibration or held-out task. |

`ahl evaluate --config FILE --area ID [--area ID ...]` selects the exported manifest's
tasks tagged with any selected area and sizes the trial ceiling to the selection. The
configuration must pin the bank used to produce the manifest. Selection is rejected if
it would narrow an already frozen experiment plan; create a new plan for that task set.
The original manifest is never rewritten. Without `--area`, all manifest tasks run.
Repeat `--area` or separate IDs with commas on both commands. The receipt records `area_selection` and, for paired runs with declared areas, `area_gate`.
Changed inputs, unqualified isolation and sequential runs are not measured by this gate. A scored regression returns
`area_regressed` and writes `investigation.json`; this gate grants no adoption authority.

## The gate block

Schema `ahl-area-gate-v1`, written as `area_gate` in the receipt of a `--single-pass` run
(`workflow.json`) for a project that declares areas, and as `area_gates` entries (with an
`evaluation_id`) in the receipt of a loop run, the default of `ahl improve`. `status: blocked`
with `reason: area_regressed` is the gate's own verdict on both routes; the typed outcome of the run
is `area_regressed` only in a single pass and `loop_regression` in the loop.

| Field | Meaning |
| --- | --- |
| `status` | `blocked` (a scored area regressed), `passed` (at least one scored area and none regressed) or `not_measured` (no area was scored). |
| `reason` | `area_regressed`, `no_area_scored`, or `null`. A vacuous evaluation reports `status: not_measured` with reason `evaluation_vacuous`. |
| `remediation` | The one step for `area_regressed`, otherwise `null`. |
| `regressed` | Ids of the scored areas that regressed. |
| `not_measured` | Ids of the areas that were provisional or without evidence. |
| `selected` | The `--area` ids, or an empty list for every declared area. |
| `margin`, `alpha`, `min_roots` | The regression margin of the run, the interval level and the roots an area needs (2). |
| `areas_examined`, `multiplicity` | The number of areas gated, and, from two areas, a note that no interval is corrected across them. |
| `areas[]` | One row per gated area in declared order, below. |

An area row has `id`, `state`, `reason`, `tasks` and `roots` (distinct tasks and roots tagged
with the area), `trials` (two per tagged pair, so a task in two areas counts in each),
`delta` (`mean`, `low`, `high` of the paired root difference, or `null`),
`flipped_to_fail`, `flipped_to_pass` and `regressed` (`true` or `false` for a scored area,
`null` otherwise).

## States

| State | Reason | Cause |
| --- | --- | --- |
| `scored` | none | Every pair of the area known on both arms and at least `min_roots` distinct roots. |
| `provisional` | `unknown_outcomes` | A pair of the area has an outcome that is not an explicit pass or fail. |
| `provisional` | `below_minimum_roots` | Known outcomes but fewer than two roots. |
| `no_evidence` | `no_tagged_pair` | No pair carries the area. |
| `no_evidence` | `no_known_outcome` | No pair of the area has a known outcome on both arms. |

## How it decides

The numbers are those of the [before/after guard](before-after-guard.md): the paired root
means and the `weighted_root_Hoeffding` interval, restricted to the pairs the area tags.
No statistic is added. A scored area regresses when its observed paired delta is below
`-margin`, the same rule the guard applies to the pooled result, so a small area cannot hide
a loss behind a wide interval. Only a scored area can pass or regress: an unknown outcome,
a thin area and an area with no pair are reported as not measured and never as a pass, a
failure or independence.

## Where it applies

| Surface | Effect |
| --- | --- |
| `ahl improve` (the default loop, on a pinned bank) | Each paired evaluation adds an `area_gates` entry and sets `regressed_areas` on its round; a non-empty list is the `area` regression, which stops the loop with the criterion `regression` whatever the policy or the pooled score. The run ends status `not_accepted`, reason `loop_regression`, exit 3, and `loop.remediation` (also `exit_receipt.next_step`) names the `regressed_areas` and `area_gates` evidence. |
| `ahl improve --single-pass` | `area_gate` in the receipt. A run that ended `evaluation_failed` on its final attempt becomes `blocked` / `area_regressed` (exit 3) with an `investigation.json` and no recommendation. A pooled `candidate_regressed` keeps its own reason. |
| `ahl evaluate` | A paired configured run writes `area_gate` to `results.json`; a scored regression blocks the run with exit 3. |
| `ahl score --area` | The report adds `multiplicity` (the number of declared areas examined) and prints it in Markdown, when it is narrowed to fewer areas than are declared. |

`area_regressed` and the `area_gate` block belong to the single pass only, and `loop_regression`
with `area_gates` to the loop only; neither route writes the other's reason or block. The
`--area` selection and its typed reasons above are checked before either route starts, so
they are the same on both.

The gate reads `areas` tags from the exported manifest, which carries them for a tagged bank;
an untagged bank, or a project without `areas`, keeps the receipt it had. The receipt is
private like every other, and the gate grants no recommendation, promotion or publication
authority.
