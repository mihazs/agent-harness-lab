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
   selected cache bytes, records the exact claim bytes and commits a proposed case.
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
5. `coverage --cache CACHE_JSON` runs the same checks and adds a bounded
   `evidence-coverage-v1` summary: source kinds and record types present, sources
   lacking an active snapshot, execution metadata, evidence graph or complete
   capture, join claims by status, reviewed links by relation, contradicted and
   unverified outcome reports, conflicts and stale cases. Sources whose capture
   coverage was never declared are listed as `capture_coverage_undeclared`, and
   `capture_completeness` stays `unknown` with `target_project_binding`
   `unverified`. Contradictions are investigation leads. The report contains
   counts and hashes only, keeps `independent_roots` null, and never labels a task a success or failure.

The cache locator must match the one recorded for a case. Quarantine, rejection
and retirement can still be recorded if that cache is missing or stale; they do
not need to read it. Rejection and retirement are terminal. Quarantine retains
prior decisions and can be followed by rejection or retirement. A changed claim or
cache requires a new proposal rather than overwriting the earlier one.

<a id="reported-and-observed-outcomes"></a>
<a id="exact-json-contracts"></a>
<a id="applicability-and-conflict-handling"></a>
<a id="persistence-limits-and-privacy"></a>

## Reference and related material

The [source lineage reference](source-lineage-reference.md) describes the exact JSON
contracts, [reported and observed outcomes](source-lineage-reference.md#reported-and-observed-outcomes),
applicability, persistence, exit statuses and privacy limits.

Run `task test:sources:lineage`. The opt-in
`task test:sources:lineage:compat BASELINE=/path/to/frozen/baseline` compares exact
regression event bytes and transitions with an explicitly frozen source baseline.
Installed source tests include the ordinary lineage controls; actual kernel
regression canaries separately exercise the reused persistence path.
