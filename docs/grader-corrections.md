# Frozen grader-correction audits and operator review

Version 0.46 adds `ahl grader-correction`. It freezes a proposed replacement for
one approved development/construction task, executes old and replacement graders
against the same five controls, and records operator artifact review of sealed
observations. It preserves the parent bank and all task/root/exposure metadata.

This workflow records a grader-defect proposal. It requires no declaration that a
historical task failed. For example, an old textual JSON comparison can accept
the canonical complete control while rejecting a semantically equivalent valid
alternative. A replacement structural comparison should accept both and reject
broken, partial and superficially correct but damaging artifacts. Missing grading
dependencies yield `grader_error` with unknown outcomes.

All cases remain private. Historical task outcomes, native provenance and root
independence remain unknown/unverified. Reviewed cases record operator attestation;
they do not change a bank, approve publication or enable automatic promotion.

## Freeze a declaration

Prepare a version 1 declaration with exactly these fields:

| Field | Required value or meaning |
| --- | --- |
| `schema_version` | Integer `1` |
| `parent` | Exactly `bank`, `sha256`, `task_id`; exact parent manifest identity and selected task |
| `replacement` | Exactly `bundle`, `sha256`, `grader_version`; a distinct bundle and version |
| `controls` | Five `{state, fixture, digest}` entries |
| `image_sha256` | Required pinned runtime identity for both audits |
| `evidence_refs` | Nonempty bounded `{ref, sha256}` source references |
| `reason` | Nonempty bounded single-line explanation |

The parent bank, replacement bundle and control paths resolve within the
declaration's directory. The parent's own fixture/oracle paths retain their normal
bank-relative meaning. Parent traversal and symlink paths reject. The task must
have approved source permission and remain in construction/development; protected
partition correction is outside this version's contract.

The controls must contain exactly `broken`, `partial`, `complete`,
`alternative_valid`, and `superficial`. Their declared expectations are false,
false, true, true, and false respectively. Grade observable artifact state; a
self-reported preservation/safety flag is insufficient for a preservation boundary.

Obtain the exact parent hash from `ahl bank --manifest /path/to/bank.json`.
Use `task input:digest INPUT_DIR=/path/to/bundle-or-control` for the existing
file-tree identities. These inherit the current digest format, including its
empty-directory/root-mode limitations.

```sh
ahl grader-correction --store /path/to/private-corrections propose \
  --spec /path/to/correction.json --author OPERATOR_LABEL
```

The controller copies the exact declaration, parent manifest, selected task
fixture, old/replacement bundles and controls into an owner-only immutable case.
Subsequent source edits do not change those snapshots. Applying a correction will
need a separate fresh parent check; this proposal makes no current-parent claim.
Cases are bounded to 128 directories, snapshots to 60 MiB/3,500 files before
metadata, and the existing per-tree bounds still apply. Keep the store outside
selected source trees and runtime views.

## Execute the controls

```sh
ahl grader-correction --store /path/to/private-corrections audit --case CASE_SHA256 \
  --cgroup-root /path/to/delegation \
  --image-root /path/to/runtime --image-sha256 IMAGE_SHA256 \
  --disk-bytes 67108864 --budget-seconds 60
```

The audit uses the existing artifact extractor and fresh pinned offline grader
workers. There is no native-agent, model or network fallback. Both graders use
the same selected image and one absolute deadline, including evidence sealing.
All controls freeze and materialize before dispatch. The old audit stops at its
first failed check; the replacement must pass all five. Receipts retain actual
grader identities, expected/observed outcomes, raw/materialized control hashes
and worker attestations.

Successful replacement controls produce `audited` evidence. Operator approval also
requires an observed old control mismatch or grading error. Passing ties preserve
their observations but cannot gain positive correction review. Backend
unavailability and budget/timeout exhaustion remain retryable `reported` cases;
an old blocked audit and a passing replacement still need a fresh complete audit.
Invalid replacement graders are quarantined with their observed status retained.

The controller syncs and seals actual report bytes and control observations before
recording `audited`. A supplied successful JSON receipt cannot qualify. Interrupted
audits stay `auditing`; explicitly abandon them using a review record with
`decision=reported`, then retry under a fresh attempt directory. Surviving files
are never automatically adopted. Corrupted snapshots or seals refuse review.

## Inspect and review

```sh
ahl grader-correction --store /path/to/private-corrections show --case CASE_SHA256
ahl grader-correction --store /path/to/private-corrections review --case CASE_SHA256 \
  --record /path/to/operator-review.json
```

`show` returns the current event/evidence identities, old/replacement statuses,
private attempt location and proposed bank diff. The sealed receipt is retained
under `cases/CASE_SHA256/attempts/ATTEMPT/evidence.json`, with separate old and
replacement `review.json` files for artifact inspection.

Use the existing [operator-review record fields](regression-review.md):
`schema_version`, `case_id`, `expected_event_sha256`, `evidence_sha256`, `decision`,
`reviewer`, `evidence_refs`, `reason`. The reviewer object requires `identity`,
`source=operator_attestation`, `provenance`, `basis_kind=independent_artifact_review`
and `basis`. Positive review requires a separate author/reviewer label, nonempty
artifact references and exact current event/evidence identities. A fresh model
session alone is rejected as the review basis. Identity and independence remain
operator attestations. Persisted review records are revalidated on reads.

Negative decisions `quarantined`, `rejected` and `retired` retain provenance.
`reported` is only available to explicitly abandon an unfinished audit. Reads and
successful operations return 0; audit incompleteness/busy state returns 3; a
quarantined audit returns 1; invalid input returns 2.

## Remaining application boundary

A reviewed correction remains a proposal with `bank_replacement=false` and a
proposed diff, including exact old/new grader identities and preserved fixture,
root/family/project/time/partition metadata. Approved immutable bank replacement
and its coordinated invalidation/fresh both-arm rerun remain subsequent work in
issue #8. The existing [history quarantine](oracle-history.md) can separately
invalidate a defective bundle's recorded comparisons. This command does not
relax [regression-bank admission](regression-admission.md) duplicate/exposure checks.

Run `task test:evaluation:corrections` and
`task test:evaluation:grader-controls` for native contracts. The separately selected
`task test:sandbox:kernel:corrections` exercises actual pinned old/replacement
workers and operator review using independently synthetic fixtures. No live
model effectiveness, historical failure or general grader correctness is claimed.
