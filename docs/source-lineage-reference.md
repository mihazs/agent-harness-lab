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
| `relation` | `same_observation`, `derived_from`, `same_execution`, `test_result_for` or `independent_case` |
| `author` | Operator identity label |
| `evidence` | Nonempty array of distinct cache `RecordRef`s |

`derived_from` is directed: the left record derives from the right record.
`same_execution` asserts that records concern the same execution. A reviewed
`test_result_for` asserts that one test case belongs to the test suite referenced
by the other source's report in that execution. These are operator attestations,
not native proof. Independent-case assertions do not create links or become transitive.

### Reported and observed outcomes

The `sources status` result shows unlinked structured outcomes for its selected source.
The lineage `query` result includes `outcome_correlation` when selected source records carry
structured outcomes. Test-result imports record parsed JUnit and pytest case statuses:
`passed` and `failed` become success and failure, while skipped, errored or unknown
cases stay unknown. Normalized `ahl-metadata` sources may carry a schema-2 record
with an `outcome` object containing `kind: reported`, `value: success|failure|unknown`
and `provenance_tier: agent_report|memory_summary` and `scope: test_suite|unknown`. Schema-1 records retain their
original representation and have no outcome. Native capture without a structured
outcome does not acquire one by parsing message bodies.

The correlation keeps source revision and provenance on `reported_outcome` and
`observed_outcome`. Only a direct, applicable, reviewed `test_result_for` link associates a case with
its reported test suite; `same_execution` alone does not establish the same target.
A failed case contradicts an all-passing suite report and yields observed failure,
even if other cases passed. Passing cases alone do not establish full suite coverage,
so a passing-only or unknown-scope report remains `unverified`.
Unknown, stale and ambiguous records never become verified outcomes. This private
report does not assert independent roots, task success, causation or authorization.

Outcome labels retain source-level meaning: a test result reports one test case on
a caller-declared commit; it does not prove the result belongs to an agent task.
The normalized agent report and lineage review are source declarations, not
authenticated facts. Only reviewed correlations against the same frozen cache are
shown. For an existing schema-1 test case, reimport can enrich outcome metadata only
when the structured test-result proof hashes to the exact existing revision and its
status agrees with the outcome. It preserves the original record identity, provenance
and collection time. Frozen prior cache snapshots and lineage reviews are unchanged;
the changed current cache makes old reviews stale, requiring a new review.

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
against live upstream sources by this operation. Within the selected cache, claim
and approval evidence must be present, unsuperseded and unambiguous, just like the
linked endpoints. A historical claim or review that cites an unavailable revision
remains inspectable, but a current query reports it as stale and excludes its link.
This stricter check can make previously applicable reviews stale; it does not
assert that the historical material was false or verify upstream freshness.

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
separate: their candidate receipts do not enter these components or reviews. The
[preregistered utility study](source-contracts.md#preregistered-utility-study-against-a-session-only-baseline)
scores resolver outputs against a session-only baseline on a fixture. The
[near-duplicate review queue](#near-duplicate-review-queue) lists lexically similar
records for review. Broader native cross-source resolution, corpus integration and
immutable regression admission remain work under #58/#8.

<a id="near-duplicate-review-queue"></a>

## Near-duplicate review queue

`ahl sources lineage ... near-duplicates` lists pairs of distinct source records whose
caller-supplied text is lexically near-identical (`cross-source-near-duplicate-queue-v1`,
rule `word-punctuation-3gram-jaccard-v1`). The text file is private operator input: it is
read once, never fetched, stored or printed, and its binding to a record is
`caller_declared`, because cache revisions hash adapter metadata rather than the raw text.

| Field | Meaning |
| --- | --- |
| `threshold_permille` | 800 by default; a pair is listed at or above this word/punctuation 3-gram Jaccard overlap (NFC, case-folded) |
| `pairs[].content_relation` | `exact` (same bytes), `normalized` (same word/punctuation sequence) or `near` |
| `pairs[].cautions` | `negation_differs` (English negation cues differ) and `identifiers_differ` (tokens containing a digit differ) |
| `pairs[].cross_source` | Whether the two records come from different sources |
| `pairs[].queue_status` | `pending_review`, `covered_by_reviewed_link`, `adjudicated_independent_case` or `conflicting_review` |
| `pairs[].evidence_class` | Always `heuristic_candidate` |
| `counts` | Items, candidate and exposed-item counts, per-status and per-relation counts, cautioned pairs and skipped same-record revisions |
| `lineage_grouping_revision_sha256` | The `query` revision the statuses were computed against; null while the review store does not exist yet |
| `lineage` | `store` (`read` or `absent`) with the query's conflict and stale-case counts |

Revisions of one native record are history, not duplicates, and are skipped and counted.
A reviewed component or independent-case assertion changes only a pair's status: the
candidate set and its similarity do not change, so repeated text exposure stays reported
after a reviewer decides the records are independent. A review recorded against a different
revision of a record does not cover the current revision. The report never creates a link,
merges components, changes a component count or sets `independent_roots`; those stay a
reviewer's `propose`/`review` decision. A paraphrase that shares no wording is not found,
and the negation cues cover English only, so an empty queue does not show independence.
The pair search has no implicit item ceiling. Exit status 3 means pairs await review or the
query found stale cases or conflicts.

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
