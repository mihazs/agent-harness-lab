# Source lineage review reference

[Review source relationships](source-lineage-review.md) · [Source evidence contracts](source-contracts.md)

## Exact JSON contracts

Every listed field is required in version-1 JSON. `RecordRef` has exactly
`source_sha256`, `native_id_sha256`, `revision_sha256` and `schema_version: 1`.
Use the exact private cache metadata; do not infer references from names, similar
text, timestamps, embeddings or an agent's explanation.

| Claim field | Value |
| --- | --- |
| `schema_version` | Integer `1` |
| `left`, `right` | Two distinct exact `RecordRef` objects |
| `relation` | `same_observation`, `derived_from` or `independent_case` |
| `author` | Operator identity label |
| `evidence` | Nonempty array of distinct cache `RecordRef`s |

`derived_from` is directed: the left record derives from the right record.
Independent-case assertions do not create links or become transitive.

| Review field | Value |
| --- | --- |
| `schema_version` | Integer `1` |
| `case_id` | Exact case hash |
| `expected_event_sha256` | Current journal-head hash from `show` |
| `cache_sha256` | Captured cache hash from `show` |
| `decision` | `reviewed`, `quarantined`, `rejected` or `retired` |
| `reviewer` | Operator identity label |
| `source` | `operator_attestation` |
| `provenance`, `basis`, `reason` | Nonempty single-line text, without a length ceiling |
| `basis_kind` | `independent_metadata_review` for approval; structural label for intervention |
| `evidence` | Distinct cache `RecordRef`s; nonempty for approval |

Labels retain the source-label character rules without a length ceiling. Different author and
reviewer strings do not authenticate different people. Evidence references and
review provenance remain declared citations; they are not independently checked
against live upstream sources by this operation.

## Applicability and conflict handling

Applicability is always `selected_frozen_cache_only`, with native freshness and
independent roots unverified. Any cache-byte change, including an unrelated append
or formatting change, makes older cases stale for a current query. The original
snapshot and decisions stay intact. This intentionally conservative rule avoids
silently carrying an old decision into a changed evidence population. It can
require new reviews even when the two endpoints themselves survived unchanged.

Reviewed same-observation and derived-from links form conservative correlated
components. Component count is not an independent sample count. Derived edges
retain their direction. An independent-case assertion within a correlated
component is a conflict; it never splits the component. Derivation cycles are
checked after collapsing only same-observation aliases, so aliases cannot hide a
cycle and ordinary derivation edges do not automatically create one.

`grouping_revision_sha256` binds scope, selected cache bytes and every recorded
case head. Changes to decisions or the selected cache produce a new query revision.
Earlier decisions and experiment manifests are not retroactively rewritten.
The existing task-bank lexical receipts remain heuristic/pending; this command
neither clears content exposure nor upgrades bank eligibility. The offline
Git/GitHub [commit join](source-contracts.md#offline-gitgithub-commit-join) and
[PR commit-role join](source-contracts.md#offline-pr-commit-role-join) are
separate: their candidate receipts do not enter these components or reviews. Broader native
cross-source resolution, corpus integration, diagnostic-utility evaluation and
immutable regression admission remain work under #58/#8.

## Persistence, limits and privacy

The recognized store must be outside its cache, owned by the current user and
mode 0700. Metadata and snapshots are created with mode 0600. Read operations
cannot initialize an unrelated directory. Locks, strict JSON decoding, immutable
append checks and hash chains reuse the regression-review journal implementation;
regression transitions and event bytes remain unchanged.

Analysis cases, snapshots, claim/review files and event metadata have no fixed
count or byte ceiling. The lineage journal explicitly selects unlimited storage;
other private-journal callers keep their own limits. Scope fingerprints, owner-only
storage, immutable hashes, exact event chains and review transitions still apply.
Interrupted or uncertain writes require inspection; nothing automatically retries,
adopts a partial proposal or erases private history. A local hash chain cannot
authenticate the host account or prevent its complete rewrite of a store.

Successful operations return 0. Query returns 3 for stale cases or conflicts, and
lock contention returns 3 with a busy result. Invalid/unavailable inputs return 2
with a generic diagnostic. Exit 0 never certifies lineage or task outcomes.

All snapshots, reviews, hashes, components and counts remain private-derived
material. Local output does not authorize committing, exporting or publishing it.
The tests use independently synthetic metadata only.
