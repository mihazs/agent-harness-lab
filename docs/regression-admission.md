# Immutable private regression-bank admission

Version 0.44 adds explicit acceptance of a [reviewed regression case](regression-review.md)
into a new private development-bank revision. It requires the existing controlled
offline reproduction, intact sealed evidence and operator artifact review. It
never admits a case from a caller-supplied success receipt or an `accepted` label.

The result is development/regression data. Historical authentication, native
provenance, independent roots and certification eligibility remain unverified or
false. Acceptance does not authorize publication or automatic promotion.

## Accept a reviewed case

Keep the P/D/R declarations and complete private review store. After reproducing,
drafting and reviewing the case, obtain its current event/evidence identities:

```sh
ahl regression-review --store /path/to/private-store show --case CASE_SHA256
```

Prepare an explicit admission record. Version 1 requires exactly these fields:

| Field | Required value or meaning |
| --- | --- |
| `schema_version` | Integer `1` |
| `case_id` | Exact reviewed case identity |
| `expected_event_sha256` | Current positive-review event hash |
| `evidence_sha256` | Sealed evidence hash from the case |
| `parent_bank_sha256` | Exact bytes of the selected parent bank manifest |
| `parent_revision_sha256` | Controlled parent revision identity, or `null` for a legacy parent |
| `operator` | Exactly `identity`, `source`, `provenance`, `basis`; source is `operator_attestation` |
| `purpose` | `development_regression` |
| `provenance_binding` | JSON `null`; this version has no native-reference bridge |
| `reason` | Nonempty bounded single-line explanation |

All operator text is bounded. This record attests to an operator decision; distinct
identity strings do not authenticate different people. Use `show` again after a
concurrent case decision, rather than reusing a stale event hash.

```sh
ahl regression-review --store /path/to/private-store accept \
  --case CASE_SHA256 --parent-bank /path/to/parent/bank.json \
  --record /path/to/private-admission.json
```

Acceptance reads only these explicitly selected local inputs. It rechecks the
sealed controls, replay phases, actual artifacts and positive review, then copies
the selected parent tasks, minimized fixture, unchanged oracle and all five raw
controls. The raw fixture and materialized minimized fixture retain separate
identities. An observed repair is not required to admit a reproduced unfixed
failure as development data.

The new task retains its declared root/family/project/time metadata, receives a
case-derived task ID and has `partition=development`, `review_status=accepted`
and `independence=unverified`. Opaque source citations remain opaque; their hashes
are not interpreted as source-native record identities. Known citation overlap
with conflicting root/project labels, exact duplicates and normalized fixture
copies reject rather than creating supposedly independent cases. Unknown semantic
relationships remain unknown. Lexical checks do not establish causal lineage.

## Revision and transaction boundary

The controller creates `revisions/REVISION_SHA256/bank.json` under the private
store. A versioned descriptor binds the immutable parent identity, exact bank
manifest, payload tree, case review/evidence and control catalog. There is no
mutable latest pointer. Select an exact revision path explicitly for validation,
export or execution preparation.

The controller validates and syncs the prepared revision before appending the
case's accepted event. Only that matching event authorizes the revision. An
interrupted publication can leave an orphan directory; validation and export
reject it. Retrying the identical committed request returns the existing revision.
An uncommitted orphan is retained for intervention, not overwritten or silently
adopted. The nonblocking store lock prevents concurrent writers from duplicating
or replacing acceptance history.

Child revisions contain their own asset/control copies and parent manifest bytes.
They do not require physical ancestor revision directories to remain available.
Inherited case journals remain part of the authority boundary: their exact accepted
event hashes are pinned, while each new revision's own marker is resolved through
its descriptor to avoid a hash cycle. Quarantining or retiring an inherited case
makes descendants ineligible for new export/audit. Child-owned frozen bytes are
always checked; changing a parent source or current eligibility during admission
cannot commit an eligible child using a stale preflight result.

Read validation never initializes a store. Controlled revisions must remain at
their original private controller paths. Copies of their manifests alone cannot
establish acceptance. The store admits at most 32 revision directories, including
orphans. Revision payloads are bounded to 64 MiB and 4,096 files; individual source
trees and metadata retain their existing stricter bounds. Metadata files use mode
0600 and private directories use mode 0700; copied assets preserve hashed modes
beneath those private ancestors.

## Use reviewed controls without changing the oracle

`ahl bank --manifest REVISION_BANK --export NEW_OUTPUT` validates acceptance and
current eligibility before creating the output. Controlled exports preserve each
original grader digest and copy raw controls to `controls/TASK_ID/INDEX`. Source
materialization occurs there, never inside the frozen oracle. All five reviewed
controls are retained; only broken/partial/complete are mapped into the legacy
`grader_self_tests` field. Unchanged legacy parent tasks retain their original
control set. The exported manifest includes `bank_revision_sha256` alongside the
bank and lexical grouping identities.

Explicit cooperative/sandbox grader audits use the same reviewed controls.
Wholly legacy banks retain their existing `controls.json` behavior. Acceptance
does not make a private bank certification-eligible, and a successful CLI exit is
not evidence of independent task roots.

## Limits and validation

Already exported manifests and completed comparisons are not retroactively
rewritten. Oracle correction/replacement, comparison invalidation with mandatory
both-arm reruns, and the explicit opaque-to-native provenance bridge remain
subsequent work in [issue #8](https://github.com/mihazs/agent-harness-lab/issues/8).
This milestone does not close that issue or provide authenticated lineage.

Run `task test:evaluation:admission` for native contract, profile-race and external
control tests. `task test:evaluation:admission:compat BASELINE=FROZEN_ROOT` compares
legacy validation/export with an explicit baseline. The separately selected
`task test:sandbox:kernel:regressions` exercises actual pinned offline workers and
graders through review and acceptance using synthetic fixtures and operator data.
Mocked controls establish orchestration, not kernel containment or human identity.

Private declarations, case identifiers, source paths, raw runs, receipts and derived
summaries must remain private. No operation here authorizes committing or publishing
those artifacts.
