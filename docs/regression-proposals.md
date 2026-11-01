# Declaring a regression reproduction

New in 0.38, `ahl regression --proposal PATH` validates one explicitly authorized
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

Fixture conversion, objective grader falsification, A/A and matched comparisons,
review transitions, root/exposure checks and immutable bank admission are separate
remaining gates. Existing `ahl bank` commands are unchanged. Run the native
contract checks with `task test:evaluation:regressions`; they also run against
the installed wheel and extracted release archive.
