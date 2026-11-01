# Declaring a regression reproduction

New in 0.38, `ahl regression --proposal PATH` without `--draft` validates one explicitly authorized
JSON record and prints a compact local receipt. Exit 0 means the declaration
matches the schema; malformed, unapproved or contradictory records exit 2.
It reads only that bounded regular file. References are opaque identifiers: no
referenced file, URL, trace, command, model or incident corpus is opened.

This is the first reproduction-contract slice of the reviewed regression-bank
workflow. It creates no files and never changes the active bank. Every receipt
keeps lifecycle `reported`, evidence verification `not_executed`, and task-failure
verification, admission, publication and automatic promotion false. The tool
does not verify that an asserted permission or source classification is truthful.

## Version 1 record

The exact top-level fields are:

| Field | Contract |
| --- | --- |
| `schema_version` | Integer `1` |
| `proposal_id`, `root_id`, `family` | Nonempty local identifiers, at most 128 characters |
| `mechanism`, `expected_boundary` | Single-line explanation and observable failure boundary |
| `source` | `kind`: `synthetic`, `public` or `private`; `permission`: `approved`; `evidence_refs` and `trace_refs` |
| `before`, `after` | `harness_sha256`, `config_sha256`, `memory_sha256`, and `evidence_refs` |
| `task_start` | Exact task-start reference, or `{"ref": null, "sha256": null}` when unavailable |
| `environment` | `image_sha256`, `runtime_ref` and nonempty `requirements` list |
| `reproduction` | `disposition`, `original_outcome`, `replay_outcome`, `run_refs`, `boundary_refs`, `reason` |

Each reference is exactly `{"ref": "opaque-local-reference", "sha256": "..."}`,
with a 64-character lowercase hexadecimal digest. Reference lists have at most
32 entries, with unique references within each list. Empty lists preserve missing
evidence. Known snapshot digests require observation references. Unknown snapshot
digests, image identity and runtime reference stay JSON `null`; the tool never
substitutes the current environment or reconstructs an earlier state. Use the
digest of a recorded empty configuration or memory snapshot when its emptiness
was actually observed. The input limit is 256 KiB; text is bounded at 4096
characters and cannot contain control characters. Unknown fields are rejected.

Allowed dispositions are `reproduced`, `not_reproduced`,
`environment_or_grader_defect`, `unsupported` and `insufficient_evidence`.
Both outcomes use `task_failure`, `task_success`, `expected_rejection`,
`environment_or_grader_defect` or `unknown`. A `reproduced` declaration requires
two task-failure outcomes, both snapshot identities, task-start/runtime identities
and nonempty source, trace, run and boundary references. A recovered tool error
ending in task success, an expected denial or a missing final outcome cannot
satisfy that declaration. Environment/grader defects require a matching outcome
and receive `diagnostic_review` routing. Other records receive reproduction review.

The receipt lists missing evidence by field name and hashes the exact input
bytes. It omits local IDs, reference contents and explanatory prose. Even a
complete declaration still requires deterministic replay, independent artifact
checks and review. It cannot establish causality, temporal degradation, task
independence or improvement. Temporal claims additionally need an earlier valid
version and controlled comparisons; those are outside this validator.

## Privacy and remaining work

Private records and their derived receipts, hashes, summaries and aggregates stay
in private user state. A receipt's omission of prose does not authorize publishing
it. Public examples must be independently synthetic or explicitly permitted public
material. The schema's `approved` field is an operator assertion, not an access
grant or independent review.

Historical replay, A/A and matched comparisons, review transitions, root/exposure
checks and immutable bank admission remain separate gates. Existing `ahl bank`
commands are unchanged. Run the native
contract checks with `task test:evaluation:regressions`; they also run against
the installed wheel and extracted release archive.

## Auditing a frozen draft oracle

New in 0.39, adding `--draft PATH` explicitly executes offline grader controls.
Supply `--output` as a new private directory and all of `--cgroup-root`,
`--image-root`, `--image-sha256` and `--disk-bytes`. No containment profile is
discovered or relaxed. The image pin must equal the proposal's recorded image
identity. `--budget-seconds` bounds the whole operation, including preparation,
capability probing and integrity checks; default 60, maximum 300. Each grader
also has a five-second ceiling clipped to the remaining parent budget.

The version 1 draft descriptor has exactly:

| Field | Contract |
| --- | --- |
| `schema_version` | Integer `1` |
| `proposal_sha256` | SHA-256 of the exact proposal bytes |
| `task` | Existing version 1 `TaskSpec`, with fixture/grader paths relative to this descriptor |
| `controls` | Five objects, each with exactly `state`, relative `fixture` path and tree `digest` |

Reuse the existing bank's `TaskSpec` fields and tree-digest convention. The task
must retain `review_status: unreviewed` and `independence: unverified`, declare
approved source permission and nonempty bounded artifacts, and use construction
or development partition. Its root, family and source kind must match the
proposal. Its grader bundle requires `grade.py.txt` and `expected.json`; the
controller constructs the fixed Python artifact-grading command.

Controls are exactly `broken`, `partial`, `complete`, `alternative_valid` and
`superficial`. The first, second and fifth must produce valid negative grades;
complete and alternative-valid artifacts must receive valid positive grades.
All controls must supply the declared artifact files. Paths, digests and every
materialized control are validated before any grader runs. This reuses the
starter-bank data-only `source_files.json` materialization convention. The receipt
binds both original and materialized control identities. It does not run a task
executor or let a candidate edit an oracle.

Each grade runs in a fresh offline sandbox with only extracted allowlisted
artifacts and a pristine frozen oracle. The typed grading envelope, declared
grader version, pinned image checks, whole-cgroup cleanup and disk-quota
attestation must agree. A grader crash cannot count as a negative control.
Oracle/fixture/proposal changes invalidate the audit and stop remaining controls.

The owner-only output directory contains `review.json` (mode 0600), with exact
input hashes, expected/observed control outcomes, backend/resource attestations
and the elapsed budget. It excludes raw grader stdout/stderr. Existing output
directories cannot be overwritten. `grader_controls_passed` means these five
frozen controls passed; `controls_failed`, `grader_error`, `grader_timeout`,
`containment_error`, `invalid_input`, `budget_exhausted` and `blocked` retain
their distinct limits. CLI success is 0 only for passed controls, unsupported
containment returns 3, and execution failures return 1. Invalid declarations or
profiles return 2 before grading.

Passing controls does not prove that the operator-selected draft captures the
historical mechanism. Historical task-failure verification, review, independence,
admission, publication and promotion remain unestablished or disabled in the
receipt. Private inputs and all derived hashes/receipts remain private. Run
`task test:sandbox:kernel:regressions` for the separate real pinned-image/cgroup
canary; ordinary native tests simulate backend replies and cannot prove isolation.
