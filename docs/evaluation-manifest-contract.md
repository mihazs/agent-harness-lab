# Evaluation manifest contract

Reference for the manifest the evaluation-construction agent writes, the fields the
controller owns, the typed reasons for a violation, and the one repair pass.

## Who writes what

| Object | The author writes | The controller writes |
| --- | --- | --- |
| manifest | `skills`, `harness_paths`, `tasks`, `grader_self_tests`, `grader_bundles` | `max_trials`, `budget_seconds`, `timeout_seconds`, `no_nested_agents` |
| task | `id`, `split`, `fixture`, `prompt`, `artifacts` | `grader` (controlled runs) |
| `grader_self_tests` entry | `task`, `state` (`broken`, `partial`, `complete`), `fixture` | `digest` |
| `grader_bundles` entry | `path` | `digest` |

The controller computes every digest from the frozen author output it already owns. An
author never writes a hash: the construction prompt says to omit `digest`, because a hash
the author computes with its own algorithm can never be the frozen value. Every
construction path (host-grader, sandboxed-grader and controlled, with or without an
evaluation store) drops each authored bundle and control `digest` the moment the author
finishes, before the output is checked, snapshotted, sealed or published, so every later
reader sees a manifest without them. The receipt records only how many as
`authored_digest_ignored`, and the dropped value is never compared with anything. Nothing
authored replaces a computed digest. A control fixture or bundle changed after freezing
fails the run: a host-run grader that writes into its own bundle or control directory is
caught during the grader self-test as `grader_input_changed_after_freeze`, before any
trial, and a change found later reports the same reason. The controller starts host-run
graders with bytecode writing disabled, so a grader that imports a sibling module does not
change its own bundle.
Runs that grade with host-side graders (no sandbox) declare `grader` themselves, but
declared bundles are frozen the same way. The optional `{path, digest}` declaration
remains valid only where a recorded bank declares the digest (bank and replay
validation, which never drop a digest); a mismatch there is still rejected as an
integrity failure. A direct shape check that does not drop digests reports an authored
`digest` as `authored_digest_not_accepted`.

## Typed reasons

A violation reaches the user as a reason, the stage, the file, the field (names only,
never content; list positions are task or entry indexes), whether AHL, the agent output or
a project declaration is at fault, and the exact next step. The receipt carries them as `reason` and
`contract_violation`; the CLI prints them as one line. The generic remediation for each
reason is in the [exit reason table](analysis.md#exit-reasons-and-next-actions).

| Reason | Origin | Repair pass |
| --- | --- | --- |
| `evaluation_manifest_unreadable` | agent output defect | yes |
| `evaluation_manifest_field_missing` | agent output defect | yes |
| `evaluation_manifest_field_invalid` | agent output defect | yes |
| `evaluation_manifest_controller_field_supplied` | agent output defect | yes |
| `authored_digest_not_accepted` | agent output defect | yes |
| `grader_input_changed_after_freeze` | agent output defect | no |
| `evaluation_manifest_controls_incomplete` | agent output defect | yes |
| `evaluation_construction_timeout` | agent output defect | one more pass, only if the remaining budget covers it |
| `evaluation_task_infeasible` | agent output defect | no |
| `internal_contract_error` | AHL contract mismatch | no |
| `area_registry_invalid` | project declaration defect | no |
| `area_duplicate` | project declaration defect | no |
| `task_areas_empty` | project declaration defect | no |
| `task_areas_too_many` | project declaration defect | no |
| `task_areas_invalid` | project declaration defect | no |
| `task_area_undeclared` | project declaration defect | no |

An unexpected `KeyError`, `TypeError`, `AttributeError` or `IndexError` inside a stage is
caught at the stage boundary and reported as `internal_contract_error` with the stage
name; no result text is an exception message. Semantic rejections after the shape check
(a self-test that does not fail a broken control, overlapping fixtures) remain
`invalid_input` and are not repaired.

`evaluation_construction_timeout` and `evaluation_task_infeasible` come from the stage
budgets: a construction pass that overruns its time box, and a task whose `complete`
control cannot be graded within the per-trial timeout. Their rules, and the refinement
that keeps the frozen tasks, are in [stage budgets and early stops](budget-stages.md).

## Capability area declarations

A project may declare capability areas and tag bank tasks with them
([how to](declare-capability-areas.md)). A defect in either declaration is a project
declaration defect, not agent output: it is never repaired by an authoring pass, and the
next step is to edit `ahl.json` or the bank.

| Field | Where | Rule |
| --- | --- | --- |
| `areas` | `ahl.json` | Optional list of at most 32 entries; absent declares nothing. |
| `areas[].id` | `ahl.json` | Required, unique, lowercase kebab-case, at most 64 characters. |
| `areas[].label` | `ahl.json` | Required, 1 to 80 printable characters. |
| `areas[].description` | `ahl.json` | Required, 1 to 400 printable characters. |
| `areas` | bank task | Optional list of one to three distinct declared ids; absent means untagged. |

The reasons are `area_registry_invalid` (shape, size or an id or text outside the rules),
`area_duplicate` (an id listed twice in the registry or in one task), `task_areas_empty`
(an empty list or an empty id), `task_areas_too_many` (more than three tags),
`task_areas_invalid` (not a list of kebab-case ids) and `task_area_undeclared` (a tag the
project does not declare; areas are never created implicitly). Errors name the stage
(`configuration` for `ahl.json`, `bank_validation` for the bank), the file, and a field
such as `tasks[2].areas[1]`, never the offending value. Tags are checked against the
registry wherever a configured bank is validated: before `ahl improve` dispatches
anything and when a configured evaluation starts. `ahl bank` has no project, so it checks
tag shape only and lists a per-area task count; a task listed in several areas is counted
in each.

## Shape check and repair

The manifest shape is checked as soon as the author finishes, before any freezing,
grading or other stage, so a contract defect costs seconds. For a repairable reason the
author runs once more, in the same workspace and inside the same budget, with the typed
reason appended to its task. The attempt is recorded as `evaluation_construction_repairs`
in `workflow.json` (reason, stage, file, field, origin, outcome `repaired` or
`requested`). Each receipt entry carries the elapsed seconds of its construction attempt
(`elapsed_seconds`). Every terminal run receipt, including blocked and invalid ones,
carries `authored_digest_ignored` and `evaluation_construction_seconds` (0 when the
construction stage never ran), so a rejected manifest shows what it cost. A second defect ends the run with its typed reason. Sealed closed-input
stages and bank conversion allow no second pass.

## Recover from a rejected manifest

1. Read `reason`, `contract_violation.field` and `next_step` in `workflow.json`.
2. For an agent output defect, rerun `ahl improve`; the repair pass usually fixes it.
3. If the same field fails again, inspect the authored `evaluation-0/manifest.json` in
   the private run directory against the table above.
4. For `internal_contract_error`, report the stage and field; do not edit the manifest.
