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
review alone does not change a bank, approve publication or enable automatic
promotion. The explicit application and fresh-execution steps below are separate.

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

## Apply an immutable correction

A reviewed case remains a proposal until an explicit application:

```sh
ahl grader-correction --store /path/to/private-corrections apply --case CASE_SHA256 \
  --parent /path/to/parent/bank.json --history-store /path/to/private-history \
  --record /path/to/application.json
```

The strict application record has exactly `schema_version=1`, `case_id`,
`expected_event_sha256`, `evidence_sha256`, `parent_bank_sha256`,
`parent_revision_sha256` (null for a legacy bank), `history_store` (the exact
absolute selected history locator), `expected_oracle_event_sha256` (null only
when the old bundle is unregistered), `operator`, and `reason`. The operator
object contains `identity`, `source=operator_attestation`, `provenance`, `basis`.
Obtain current correction identities with `show`; obtain a registered bundle's
CAS identity with `ahl experiment-history --store HISTORY oracle --bundle SHA`.

Application revalidates the exact live eligible parent and reviewed evidence.
It durably quarantines the old bundle in the selected HistoryStore before
publishing a new immutable `grader-correction-bank-v1` revision. An unregistered
old bundle can be registered and quarantined from its verified frozen bytes;
this creates no completed comparison. Failures never restore its eligibility.
An orphan revision without its exact applied journal marker is unusable.

The task set and fixture/root/family/project/time/partition/source/artifact
policies stay unchanged. Only the selected development/construction task's
oracle digest/version and separate correction metadata/controls change.
Untouched protected siblings retain their policy; the corrected bank remains
ineligible for certification. All five reviewed raw controls export outside
the frozen oracle. Original admission markers, ancestor correction markers and
their negative-history decisions remain separate live authorities. Their
revocation or disappearance blocks use, without requiring physical ancestor
revision directories. No re-admission or duplicate bypass occurs.

## Execute a fresh full paired rerun

First inspect the old controller-bound declaration, even if its bank/oracle is
now invalidated:

```sh
ahl experiment-history --store /path/to/private-history historical-plan --run OLD_RUN
```

This private metadata read does not approve current applicability or analyze
old outcomes. Use its exact `run_id`, `event_sha256`, `inputs_sha256`,
`plan_sha256`, and `evidence_sha256` in the rerun request's `prior` object.
The latter is null for an incomplete v2 run: only its pinned initial task and
materialized-fixture declarations are usable, and unsealed result JSON is ignored.
Incomplete v1 runs without fixture pins refuse. Sealed v1 runs preserve recorded
byte identity; missing mode/native identity never becomes verified evidence.

The strict rerun record has exactly `schema_version=1`, `case_id`,
`expected_event_sha256`, `evidence_sha256`, `bank_sha256`, `revision_sha256`,
`history_store`, `prior`, `operator` (the same four provenance fields), `reason`.
The case CAS is refreshed after every attempt. The bank and history must be the
exact applied revision and selected history from application.

```sh
ahl grader-correction --store /path/to/private-corrections rerun --case CASE_SHA256 \
  --record /path/to/rerun.json --baseline /path/to/original-baseline \
  --candidate /path/to/original-candidate --cgroup-root /path/to/delegation \
  --image-root /path/to/runtime --image-sha256 IMAGE_SHA256 \
  --disk-bytes 67108864 --budget-seconds 60
```

The explicit harness directories must still match the recorded byte-digest
contracts. Missing earlier inputs fail; no code or historical state is
reconstructed. The new plan preserves the original selected task set, declared
harness/runtime identities, seed, repeats, weights, margins and budgets. It
changes the oracle identity and necessary export paths, then executes the entire
new baseline/candidate schedule through `HistoryStore.run`. No old artifacts,
one-arm grades or supplied successful JSON can replace those fresh executions.
One original absolute deadline includes preparation, workers and sealing.

An old host Python interpreter path is only a historical metadata selector. If
installation relocation makes it unsupported, a lexically recognized absolute
Python selector can normalize to `/usr/bin/python3`, the grading command in the
same pinned image. The old path is never dereferenced or executed; symlink or
missing-binary state does not authenticate it. This normalization is included
in the new oracle identity and leaves `offline_executor`, image and harness
contracts unchanged. Arbitrary replacement executables are not accepted.

Receipts name the actual generated new run and plan, including incomplete and
interrupted attempts. Retries retain earlier attempts and execute afresh under
a new request CAS. A completed rerun records actual execution, not effectiveness,
native authentication, complete historical-state reproduction or causality.
Historical harness modes remain unknown; declared root independence remains
unverified. Pinned fixture modes cover regular files in the existing digest;
root/empty-directory modes remain unrecorded. Current bank/oracle gates still apply to guarded analysis. Old
invalidation stays durable even when the new execution fails. No automatic
promotion or publication is enabled. Broader adversarial coverage remains open
in issue #8.

Run `task test:evaluation:corrections` and
`task test:evaluation:grader-controls` for native contracts. The separately selected
`task test:sandbox:kernel:corrections` exercises actual pinned old/replacement
workers and operator review using independently synthetic fixtures. No live
model effectiveness, historical failure or general grader correctness is claimed.
Run `task test:evaluation:correction-application` for the synthetic application
and entire paired-execution controls.
The opt-in `task test:sandbox:kernel:correction-application` exercises actual
old comparison, audit/review/application and fresh full paired execution in a
disposable delegated scope. Installed layouts support
`test:evaluation:correction-application:installed` with explicit `LAYOUT_DIR`,
`EXPECTED_PACKAGE_ROOT`, and installed `PYTHON`; the kernel target accepts the
same layout settings. These controls use independently synthetic fixtures only.
