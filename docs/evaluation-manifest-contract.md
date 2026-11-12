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
author never writes a hash. In a controlled run an authored `digest` is rejected as
`evaluation_manifest_controller_field_supplied`; a host-grader run accepts a supplied
bundle digest only when it equals the computed one. Nothing authored replaces a computed digest.
A control fixture or bundle changed after freezing fails the run before any trial.
Runs that grade with host-side graders (no sandbox) declare `grader` themselves, but
declared bundles are frozen the same way.

## Typed reasons

A violation reaches the user as a reason, the stage, the file, the field (names only,
never content; list positions are task or entry indexes), whether AHL or the agent output
is at fault, and the exact next step. The receipt carries them as `reason` and
`contract_violation`; the CLI prints them as one line. The generic remediation for each
reason is in the [exit reason table](analysis.md#exit-reasons-and-next-actions).

| Reason | Origin | Repair pass |
| --- | --- | --- |
| `evaluation_manifest_unreadable` | agent output defect | yes |
| `evaluation_manifest_field_missing` | agent output defect | yes |
| `evaluation_manifest_field_invalid` | agent output defect | yes |
| `evaluation_manifest_controller_field_supplied` | agent output defect | yes |
| `evaluation_manifest_controls_incomplete` | agent output defect | yes |
| `internal_contract_error` | AHL contract mismatch | no |

An unexpected `KeyError`, `TypeError`, `AttributeError` or `IndexError` inside a stage is
caught at the stage boundary and reported as `internal_contract_error` with the stage
name; no result text is an exception message. Semantic rejections after the shape check
(a self-test that does not fail a broken control, overlapping fixtures) remain
`invalid_input` and are not repaired.

## Shape check and repair

The manifest shape is checked as soon as the author finishes, before any freezing,
grading or other stage, so a contract defect costs seconds. For a repairable reason the
author runs once more, in the same workspace and inside the same budget, with the typed
reason appended to its task. The attempt is recorded as `evaluation_construction_repairs`
in `workflow.json` (reason, stage, file, field, origin, outcome `repaired` or
`requested`). A second defect ends the run with its typed reason. Sealed closed-input
stages and bank conversion allow no second pass.

## Recover from a rejected manifest

1. Read `reason`, `contract_violation.field` and `next_step` in `workflow.json`.
2. For an agent output defect, rerun `ahl improve`; the repair pass usually fixes it.
3. If the same field fails again, inspect the authored `evaluation-0/manifest.json` in
   the private run directory against the table above.
4. For `internal_contract_error`, report the stage and field; do not edit the manifest.
