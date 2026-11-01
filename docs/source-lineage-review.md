# Reviewing source relationships offline

New in 0.43, `ahl sources lineage` records explicit operator assessments of
relationships between exact source records. It uses an explicitly selected
private `SourceStore` cache, project/scope fingerprints and a separate private
review store. It reads no original source text, discovers no accounts, makes no
network/model calls and never modifies the source cache or an active task bank.

A reviewed annotation is an operator assertion over frozen metadata. It is not
native-ID authentication, proof of human independence, task success, causality or
permission to publish private-derived material.

## Operator sequence

Use this prefix, placing the store/scope arguments before the action:

```sh
ahl sources lineage --store /path/to/private/reviews \
  --project-sha256 PROJECT_SHA --scope-sha256 SCOPE_SHA
```

Append one action:

1. `propose --cache CACHE_JSON --claim CLAIM_JSON` validates and copies the same
   bounded cache bytes, records the exact claim bytes and commits a proposed case.
   The cache must already exist and validate for the explicitly supplied scope.
   Endpoints must be present, unsuperseded and unambiguous within that frozen
   cache. Evidence references must identify records in that cache. A cache change
   during capture cannot commit a claim. Equivalent claims cannot be duplicated by
   changing the author label.
2. `show --case CASE_SHA` returns the recorded state and event/cache hashes. This
   checks stored metadata and snapshot integrity, not current source freshness.
3. `review --case CASE_SHA --cache CACHE_JSON --record REVIEW_JSON` appends an
   explicit decision bound to the exact current journal head and captured cache.
   Approval additionally rechecks the selected cache bytes and evidence membership,
   requires a separate reviewer label and rejects fresh-session-only review.
4. `query --cache CACHE_JSON` returns applicable reviewed links, conservative
   correlated components, independent-case assertions, conflicts and stale cases.
   It rechecks the selected cache before returning. It does not return verified
   independent roots or authorize an execution.

The cache locator must match the one recorded for a case. Quarantine, rejection
and retirement can still be recorded if that cache is missing or stale; they do
not need to read it. Rejection and retirement are terminal. Quarantine retains
prior decisions and can be followed by rejection or retirement. A changed claim or
cache requires a new proposal rather than overwriting the earlier one.

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
| `author` | Bounded operator identity label |
| `evidence` | Nonempty array of at most eight distinct cache `RecordRef`s |

`derived_from` is directed: the left record derives from the right record.
Independent-case assertions do not create links or become transitive.

| Review field | Value |
| --- | --- |
| `schema_version` | Integer `1` |
| `case_id` | Exact case hash |
| `expected_event_sha256` | Current journal-head hash from `show` |
| `cache_sha256` | Captured cache hash from `show` |
| `decision` | `reviewed`, `quarantined`, `rejected` or `retired` |
| `reviewer` | Bounded operator identity label |
| `source` | `operator_attestation` |
| `provenance`, `basis`, `reason` | Nonempty single-line text, at most 4,096 characters each |
| `basis_kind` | `independent_metadata_review` for approval; bounded label for intervention |
| `evidence` | At most eight distinct cache `RecordRef`s; nonempty for approval |

Labels use the existing 64-character source-label contract. Different author and
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
neither clears content exposure nor upgrades bank eligibility. Broader native
cross-source resolution, corpus integration, diagnostic-utility evaluation and
immutable regression admission remain work under #58/#8.

## Persistence, limits and privacy

The recognized store must be outside its cache, owned by the current user and
mode 0700. Metadata and snapshots are created with mode 0600. Read operations
cannot initialize an unrelated directory. Locks, bounded JSON decoding, immutable
append checks and hash chains reuse the regression-review journal implementation;
regression transitions and event bytes remain unchanged.

Limits are 128 cases, eight snapshots, 64 MiB per selected cache and 128 MiB of
snapshot files per store. Claim/review files are limited to 64 KiB; complete events
are bounded before writing. Root-entry limits also bound abandoned staging
folders. Interrupted or uncertain writes require inspection; nothing automatically
retries, adopts a partial proposal or erases private history. A local hash chain
cannot authenticate the host account or prevent its complete rewrite of a store.

Successful operations return 0. Query returns 3 for stale cases or conflicts, and
lock contention returns 3 with a busy result. Invalid/unavailable inputs return 2
with a generic diagnostic. Exit 0 never certifies lineage or task outcomes.

All snapshots, reviews, hashes, components and counts remain private-derived
material. Local output does not authorize committing, exporting or publishing it.
The tests use independently synthetic metadata only.

Run `task test:sources:lineage`. The opt-in
`task test:sources:lineage:compat BASELINE=/path/to/frozen/baseline` compares exact
regression event bytes and transitions with an explicitly frozen source baseline.
Installed source tests include the ordinary lineage controls; actual kernel
regression canaries separately exercise the reused persistence path.
