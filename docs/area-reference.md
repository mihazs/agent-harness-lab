# Capability area reference

One page for the three things every area surface shares: the registry a project declares,
the evidence states a result carries and the keys that appear in the receipts. The task
steps are in [declare capability areas](declare-capability-areas.md),
[score a harness per capability area](score-by-capability-area.md) and
[aim an improvement at chosen areas](gate-adoption-per-area.md). The commands'
own details are in the [`ahl score` reference](score-reference.md) and the
[area gate reference](area-gate-reference.md).

An area says which capability a task exercises. It is separate from a task's `family`, which
stays the statistical grouping of related tasks. A task carries one to three areas and counts
in each; no result averages areas into one another.

## Registry

The project declares areas in `ahl.json` and tags bank tasks in `bank.json`. Nothing is
declared on the project's behalf.

| Field | Where | Rule |
| --- | --- | --- |
| `areas` | `ahl.json` | Optional list of at most 32 entries (`MAX_AREAS`). Absent declares nothing. |
| `areas[].id` | `ahl.json` | Required and unique. Lowercase kebab-case, at most 64 characters (`MAX_ID`). |
| `areas[].label` | `ahl.json` | Required, 1 to 80 printable characters (`MAX_LABEL`). |
| `areas[].description` | `ahl.json` | Required, 1 to 400 printable characters (`MAX_DESCRIPTION`). |
| `areas` | bank task | Optional list of one to three distinct declared ids (`MAX_PER_TASK`). Absent means untagged, and an untagged task counts in no area. |

Suggested starting points, held in `agent_harness_lab.evaluation.areas.SUGGESTED` and never
declared for you: `coding`, `ux-ui`, `docs` and `review`.

An invalid declaration or selection is a project declaration defect with one typed reason.
It names the stage, the file and a field such as `tasks[2].areas[1]` or `--area[0]`, and never
the offending value. Stages are `configuration` for `ahl.json`, `bank_validation` for the
bank, and `score_selection` and `improve_selection` for `--area`. The next step is to edit `ahl.json` or the bank; no authoring pass
repairs it.

| Reason | Cause | Stage |
| --- | --- | --- |
| `area_registry_invalid` | `areas` is not a list of at most 32 entries with exactly `id`, `label` and `description`, or an id or text breaks the rules. | `configuration` |
| `area_duplicate` | An id is listed twice in the registry, in one task's `areas` or among the `--area` arguments. | `configuration`, `bank_validation`, `score_selection` or `improve_selection` |
| `task_areas_empty` | A tagged task has an empty list or an empty id. | `bank_validation` |
| `task_areas_too_many` | A task has more than three tags. | `bank_validation` |
| `task_areas_invalid` | A task's `areas` is not a list of kebab-case ids. | `bank_validation` |
| `task_area_undeclared` | A task carries an id the project does not declare. Areas are never created implicitly. | `bank_validation` |
| `score_area_undeclared` | `ahl score --area` names an id the project does not declare. | `score_selection` |
| `area_selection_undeclared` | `ahl improve --area` names an id the project does not declare. | `improve_selection` |
| `area_selection_empty` | A selection names no executable, non-quarantined task. | `improve_selection` |

The gate adds `area_selection_requires_pinned_bank`,
`area_selection_requires_development_regression_and_heldout` and `area_regressed`; they are
listed in the [area gate reference](area-gate-reference.md).

## Evidence states

Every area result carries exactly one state, printed next to its numbers. Only a `scored`
area can be read as good, bad or regressed. Unknown capture or lineage is never turned into a
success, a failure or an independence label.

| State | Meaning | `ahl score` reasons | Gate reasons | Numbers shown |
| --- | --- | --- | --- | --- |
| `scored` | Every outcome known, at least the minimum of independent roots and, for `ahl score`, every task reviewed as independent. | none | none | yes |
| `provisional` | Enough to show an interval, not enough for a verdict. | `unknown_outcomes`, `below_minimum_roots` | `unknown_outcomes`, `below_minimum_roots` | interval, no verdict |
| `no_evidence` | Nothing was measured. A number would be a zero. | `no_experiment_task`, `no_graded_outcome` | `no_tagged_pair`, `no_known_outcome` | none |
| `blocked` | The bank or a grader is not qualified, or the experiment cannot be scored per area. | `bank_not_qualified`, `experiment_design_unsupported`, or the reason of the fixed analysis | not used | none |

The minimum of roots is the plan's `min_roots` for `ahl score` and 2 for the gate. A
provisional, unmeasured or blocked area is never a pass and never a regression. An overall
figure exists only for `ahl score`, only when every declared area is `scored` and no `--area`
narrows the report; `overall_withheld` says why it is missing.

## Receipt keys

`ahl score` prints schema `ahl-score-v1` on stdout. Top-level keys:

| Key | Meaning |
| --- | --- |
| `profile` | `ahl-score-v1`. |
| `experiment` | `id`, `profile`, `partition`, `alpha`, `min_roots` and `repeats` of the plan. |
| `arms` | The recorded arms, `baseline` and `candidate`. |
| `trials` | `planned` and usable `recorded` trials. |
| `untagged_tasks` | Experiment tasks that carry no area. |
| `areas` | One row per declared or selected area. |
| `overall` | The same row shape over every experiment task, or `null`. |
| `overall_withheld` | Why `overall` is `null`, or `null`. |
| `multiplicity` | The number of areas examined and the note that no interval is corrected, when `--area` narrows the report; otherwise `null`. |

An `ahl score` area row has these keys:

| Key | Meaning |
| --- | --- |
| `id`, `label` | The declared area. |
| `bank_tasks` | Tasks of the bank that carry the area. |
| `tasks`, `independent_roots` | Tasks and distinct roots of the experiment that carry the area. |
| `state`, `reason` | The evidence state and its reason. |
| `success` | Per arm: `success_rate`, `lower`, `upper`, `independent_roots`, `unknown_roots` and `evidence`; `null` without a known outcome. |
| `paired` | The fixed analysis: `status`, `reason` and, with an interval, `delta`, `lower`, `upper` and `independent_roots`; `null` without a candidate. |
| `critical_regressions` | Graded records flagged as a critical regression. |

The gate prints schema `ahl-area-gate-v1` as `area_gate` in the receipt of a single-pass
`ahl improve` run, and as `area_gates` entries in the receipt of a loop run. Block keys:

| Key | Meaning |
| --- | --- |
| `profile` | `ahl-area-gate-v1`. |
| `status`, `reason`, `remediation` | `blocked`, `passed` or `not_measured`, with its reason and the one step to take. |
| `margin`, `alpha`, `min_roots` | The regression margin, the interval level and the roots an area needs. |
| `selected` | The `--area` ids, or an empty list for every declared area. |
| `areas` | One row per gated area. |
| `regressed`, `not_measured` | Ids of the scored areas that regressed, and of the areas that were not scored. |
| `areas_examined`, `multiplicity` | How many areas were gated and the no-correction note. |
| `adoption_authority` | Always `false`: the gate grants no recommendation, promotion or publication. |

A gate area row has these keys:

| Key | Meaning |
| --- | --- |
| `id` | The declared area. |
| `state`, `reason` | The evidence state and its reason. |
| `tasks`, `roots`, `trials` | Tasks and roots tagged with the area, and two trials per tagged pair. |
| `delta` | `mean`, `low` and `high` of the paired root difference, or `null`. |
| `flipped_to_fail`, `flipped_to_pass` | Pairs that changed from pass to fail or the reverse. |
| `regressed` | `true` or `false` for a scored area, `null` otherwise. |

All keys are descriptive. Receipts are private like every other, and none grants
authority: `ahl score` is a recommendation-only view and the gate never adopts a candidate.
