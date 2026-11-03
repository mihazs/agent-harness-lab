# Local evaluation data interchange

`ahl bank-interchange` reads explicitly selected local data. It does not run
Promptfoo, OpenAI Evals, providers, callbacks or judges. No optional parser,
Node installation, credentials or network connection is required.

The supported profiles are deliberately small:

| Mapping format | Authorized source | Preserved semantics |
| --- | --- | --- |
| `promptfoo-cases-json-v1` | JSON array of inline `vars`/`assert` cases | Literal variables; `equals`, `contains` and textual `llm-rubric` descriptors; optional weights and case threshold |
| `promptfoo-results-jsonl-v1` | JSONL rows with `testIdx`, `promptIdx`, boolean `success` and numeric `score` | Original row pass/score, text output/error and optional grading/component results, including disagreement |
| `openai-evals-match-jsonl-v1` | JSONL `input`/`ideal` samples plus explicitly supplied canonical JSON registry metadata | Text or bounded role/content messages, all ideal alternatives and the legacy `Match` prefix-reference meaning |

These are AHL subset profile versions, not claims to support every upstream
release. The OpenAI format is the **open-source Evals registry**, not the hosted
Evals API. Registry metadata has exact fields `eval_id`, `class`, `args`, `metrics`;
the supported class descriptor is `evals.elsuite.basic.match:Match`, `args` contains
only `samples_jsonl`, and metrics are `['accuracy']`. The class and sample-path
strings are recorded as data. They are never imported or followed.

Upstream references: [Promptfoo inline cases](https://www.promptfoo.dev/docs/configuration/test-cases/),
[weighted assertions and thresholds](https://www.promptfoo.dev/docs/configuration/expected-outputs/),
[JSONL results and component claims](https://www.promptfoo.dev/docs/configuration/outputs/),
and the pinned [OpenAI Evals Match helper](https://github.com/openai/evals/blob/8eac7a7de5215c907fbddc30efdaf316913eccdd/evals/api.py).
For example, a failing weighted score of one third is truthy in Python; that does
not make it a pass. A completion beginning with an ideal answer can satisfy legacy
`Match` without being equal to that answer. Neither predicate establishes an AHL
artifact outcome. A rubric descriptor does not execute or qualify a judge.

## Explicit mapping and inspection

Create a schema-version-1 JSON mapping with exactly these fields:

| Field | Required declaration |
| --- | --- |
| `format` | One supported profile above |
| `source` | `repository`, `revision`, `format_version`, `dataset_id`, `source_kind` (`synthetic`, `public`, `private`), `permission: approved`, `dataset_license: {id, ref}`, exact raw source `sha256` |
| `review` | `identity`, `source: operator_attestation`, nonempty `evidence_refs` entries `{ref, sha256}` |
| `runtime` | `provider`, `model_revision`, `configuration_sha256`; missing values must be `null` |
| `score_descriptor` | `null` for case-only input, or `{metric, kind, scale: {minimum, maximum, unit}, aggregation, judge_ref}`; required for result input |
| `items` | One explicit mapping per source record, described below |
| `registry_sha256` | Exact canonical-registry raw SHA for the OpenAI profile, otherwise `null` |

Every item has `record` (one-based array/JSONL position), `source_item_id`,
`task_id`, `root_id`, `family`, `source_split`, `partition`, `exposure` and
`expected_boundary`. Record locators and source item IDs must be unique. Case task
IDs must be unique; result rows may share a task only with consistent root/family
mapping. Target
partition is only `construction` or `development`, and exposure is `development`.
Source split labels are retained but never grant a protected AHL role. Related
variants must keep their declared root/family; distinct IDs do not prove lineage.

Score kind is `deterministic`, `model_judged`, `aggregate` or `unknown`; aggregation
is `assertion`, `case` or `dataset`. Scale bounds and judge reference may be `null`.
Numeric scores retain their original scale; no normalization, thresholding or
`bool(score)` conversion occurs. Runtime, license, review and source-revision
claims remain operator declarations. Code-repository licensing is not the license
of an imported dataset. Missing/unverified permission, missing license evidence or
missing review evidence rejects intake rather than inventing approval.

```sh
ahl bank-interchange inspect --mapping MAPPING.json --source CASES.json
ahl bank-interchange import --mapping MAPPING.json --source CASES.json --output PRIVATE_IMPORT
```

For OpenAI registry samples, add `--registry REVIEWED_REGISTRY.json` to either
command. Supply that file explicitly; the importer never resolves a registry
class, `samples_jsonl` path or source URL. Inspection prints a bounded metadata
report without creating output. Errors omit raw source text and paths.

## Frozen private output and existing admission

Import creates an owner-only directory with raw source/mapping/optional registry
snapshots, exact evaluator/Python engine identity, `claims.json`, `report.json`
and case `proposals/`. Source bytes, mapping bytes, registry bytes and the complete
current evaluator engine determine import identity. Item identity additionally
binds its exact mapping and foreign data. Identical re-import validates existing
bytes/modes and returns the same identity: regular files must remain owned with
mode `0600`, and exactly the expected owned directories must retain mode `0700`.
Unexpected empty directories are rejected too. Changed sources, rules, registry or
engine require a different destination. Existing modified outputs are rejected.

The semantic report lists retained, dropped, missing, unsupported and inferred
mappings. No inference is made in this profile. Missing grading/component results
remain explicit. Every imported result has `ahl_outcome: null`, external unverified
provenance and `not_comparable`: there is no sealed AHL plan/runtime/oracle execution
binding. A supplied foreign pass, aggregate or continuous reward cannot manufacture
a #9 trial, controller receipt, #132 live capture or adoption authority.

Case proposals use the existing [regression proposal schema](regression-proposals.md)
with `insufficient_evidence`, unknown outcomes and missing snapshots/runtime/oracle
evidence. Validate one using `ahl regression --proposal PROPOSAL.json`. They are
reviewable intake, not active tasks or verified historical failures. Result-only
imports do not create failure proposals.

To proceed, separately provide actual reproduction snapshots, a task-start state,
runtime, fixture, independently authored oracle and all five controls. Preserve the
import evidence reference when authoring the new proposal; do not modify its frozen
snapshot. Use the existing [review/admission lifecycle](regression-admission.md)
for reproduction, draft, review, immutable development-bank admission and export.
Imported references/assertions enter a frozen grader bundle only through that
separate authored/reviewed contract. No executable grader or TaskSpec is generated.

Native controls exercise both case formats through this existing lifecycle with
separately supplied synthetic snapshots and actual executor/grader subprocesses.
Their containment attestations are simulated. Operator/native identity and
scientific independence remain unverified; the tests do not qualify native agents,
model behavior, isolation or a bank for certification.

## Rejection and limits

The importer rejects unknown fields, classes, formats, providers/setup/callback
configuration, executable assertions, reference/template evaluation in Promptfoo
variables/assertions, sensitive configuration keys, ambiguous IDs, duplicate JSON
keys, nonfinite/type-invalid numbers, malformed/truncated records, links and
exceeded bounds before publishing output. Literal source text remains data. No
socket, subprocess, dynamic class loading, remote schema, model or judge is invoked.

Each input is at most 1 MiB, nesting is at most 32 levels, and at most 1,000 records
are supported. Aggregate output is at most 8 MiB. These are intake storage limits,
not statistical sample-size recommendations. Complete JSONL prefixes do not prove
that a foreign evaluation or trajectory was complete. No controller-action or
side-effect coverage is inferred from imported text.

All outputs are private and grant neither automatic promotion nor publication.
Known import/proposal markers remain blocked by the public-export guard even if
publication/provenance flags are forged. Source acquisition (#57), external runtime
execution (#16), trajectory conversion (#38), experiment decisions (#9), genuine
curation/oracle authority and full #8 exposure/derivation remain separate work.

Run `task test:evaluation:interchange`. The canonical installed selector forwards
`PYTHON`, `LAYOUT_DIR` and `EXPECTED_PACKAGE_ROOT` and checks actual package imports.
