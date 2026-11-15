# Stage reuse planner

[Private memory store](memory-store.md) · [Evidence reuse contract matrix](memory-reuse-contract-matrix.md) · [Why reuse is split by authority](memory-reuse-rationale.md)

Reference for `agent_harness_lab.memory.reuse`, the pure planner that decides, per
deterministic stage, whether a prior output may replace recomputation, and explains
every hit and miss with a typed reason. The planner reads no store, calls no model and
authenticates nothing: the caller supplies the prior records and the results of live
authority checks, and the planner only decides. `ahl analyze --incremental` is the first
caller; see [reuse analysis work on unchanged input](incremental-analysis.md).

## Node key

`SHA256(b"ahl-stage-node-v1\0" || canonical_json(components))`. The components are the
canonicalizer version, receipt schema, stage, stage version, the sorted exact input
artifact digests, scope, config and policy.

- Canonical JSON is UTF-8 with sorted keys, no whitespace and no NaN; numbers are integers only (floats are rejected because they have no portable form), at most 64 KiB and 16 levels deep.
- Volatile fields (`timestamp`, `captured_at`, `created_at`, `updated_at`, `observed_at`, `time`, `ttl`) are rejected in any component. Capture time is recorded beside the key, never in it.
- An unsupported canonicalizer version is rejected, not guessed.
- The domain separator keeps a node key from colliding with any other SHA-256 of the same bytes.

## Stage graph

| Stage | Depends on |
| --- | --- |
| `source_status`, `harness_inventory` | none |
| `normalized_capture` | `source_status` |
| `diagnostic_process_mining` | `normalized_capture`, `harness_inventory` |
| `deterministic_grade` | `normalized_capture` |
| `statistical_report` | `deterministic_grade`, `diagnostic_process_mining` |
| `trend_view` | `statistical_report` |
| `wiki_projection` | `trend_view`, `harness_inventory` |

Cycles and edges to undeclared stages are rejected. A stage's inputs include the output
digest of each upstream stage, so a miss upstream is a miss for every dependent and an
untouched subgraph still hits. Candidate proposals and agent trials are not stages: they
are listed as `nonreplayable_live_trial` and are never reused as fresh work.

## Decision rules

Reuse needs a complete prior record whose stored key matches, identical components,
verified bytes, passing live authority and, for `source_status` and `normalized_capture`,
source freshness that an adapter verified. Failed, interrupted or unknown records are never reusable.

| Reason | Meaning |
| --- | --- |
| `hit_verified` | Reused |
| `miss_no_prior` | No complete prior record |
| `miss_input_changed` | An input digest changed, directly or through an upstream stage that is executed |
| `miss_algorithm_changed` | Stage version, config or canonicalizer changed |
| `miss_policy_changed` | Policy changed |
| `miss_scope_mismatch` | Scope changed |
| `miss_freshness_unknown` | Freshness was not verified; a TTL never counts. Reported as unknown |
| `invalid_integrity` | Stored key or output, or the bytes, failed verification |
| `invalid_oracle_quarantine` | Live oracle check failed (`deterministic_grade`) |
| `invalid_bank_authority` | Live bank check failed (`deterministic_grade`) |
| `blocked_role_access` | The role is denied; dependents are excluded too, or stay unknown when the upstream role was never checked |
| `nonreplayable_live_trial` | Live trials and proposals are historical evidence only |

A live check passes only on `True`. A missing result is unknown and listed under
`unknown`, never as a pass. An unknown stage makes every dependent unknown, carrying the
upstream reason: nothing changed and nothing ran, so a dependent is never reported as
executed or as `miss_input_changed`, and it adds nothing to the executed count.

An explicit `role: False` is checked first, whether or not a prior record exists, an input
or version changed, freshness or another check is unknown. It always excludes the stage
(`blocked_role_access`) and its dependents, and wins over an unknown upstream. Changing a grader hash (carried in config) misses
`deterministic_grade` and its dependents while the upstream capture still hits, so raw
evidence is retained but prior scoring is not silently re-authorized.

## Receipt

`ahl-stage-reuse-v1` is a dictionary with `reused`, `executed`, `excluded` and `unknown`
lists (each row: `stage`, `reason`, `node_key`, and `output` when reused), plus
`excluded_historical`, `counts` of reused and executed deterministic stages,
`reused_live_trials: 0`, `source_freshness` (`verified` or `unknown`) and
`publication_allowed: false`. Every graph stage appears exactly once.

## Stage records and the analyze integration

`agent_harness_lab.memory.incremental` runs the planner for `diagnostic_process_mining`
only, as a one-stage graph whose inputs are exact digests: the capture bytes and the
project's harness identity (a domain-separated digest of every harness file's content and
mode). The stage version is `process-graph-1`; a golden test pins the stage output per
version, so a change to normalization or the miner cannot ship without a new version.

A committed run is two artifacts in the [memory store](memory-store.md): the stage output
(the `process_mining` result in the stage's own key order, compact UTF-8 JSON that must
re-encode to itself) and a stage record (`ahl-stage-record-v1`: stage, node key, components,
status, output digest, sequence). The index row lives in the `stage_records` table. Reuse
looks up the exact node key first, so an earlier run is hit again after a change is undone,
and falls back to the newest complete record only to explain a miss. Reads re-hash both
objects; a row alone never proves reuse. A failed or interrupted run keeps an `incomplete`
record without an output, and the planner never treats it as a prior. The analyze run
commits and reads through [`agent_harness_lab.memory.commit`](#committing-to-the-store), the
one writer of this record, so a stage committed by either path is the same row for both.

## Committing to the store

`agent_harness_lab.memory.commit` writes what the planner reads. It is the only writer and
verifier of `ahl-stage-record-v1` and the `stage_records` index, and it never decides reuse
or proves a source fresh.

| Function | Effect |
| --- | --- |
| `commit_stage(store, project, parts, output, kind='analysis')` | Publishes the output bytes, then appends one record under the exact node key of `parts` with the next per-stage `sequence`. Returns the record `{node_key, components, output, status}` that `plan` takes as `prior` |
| `lookup(store, project, stage, key)` | The verified complete record of that stage under that exact key, or `None`. A record that fails its hash, its canonical form, its own key or the row that names it raises instead of returning, and a record whose object is missing raises too |
| `record_event(store, project, parts, event, code)` | Appends a diagnostic event for a stage that `failed` or was `interrupted`: status `incomplete`, no output, a short identifier `code` and never free text. `lookup` never returns it, so the stage stays a miss |
| `commit_receipt(store, project, receipt)` | Stores an `ahl-stage-reuse-v1` receipt as a canonical-JSON report |

A record is canonical JSON with `schema`, `stage`, `node_key`, `components`, `status`,
`output` and `sequence` (events add `event` and `code`). `sequence` starts at 1 per project
and stage and grows by one for every commit and event, so it orders them. `parts` must be
exactly what `components` returns for a graph stage; candidate proposals, agent trials and
any other name are refused before anything is written.

The record is the commit point. A stage killed after its output but before its record
leaves an unreferenced output (a miss, and garbage for `gc`), never a reusable record.
Committing the same key and output again returns the existing record and writes nothing;
another output under a key that is already committed is refused, not replaced. Every event
is its own record, so an event never blocks or replaces a later complete commit. Rows are
indexed from their manifests and rebuilt by `reindex` like any other record.

Committers of one project take a bounded per-project lock (`store.project_lock`) before the
store's writer lock, which also keeps `sequence` unique. Projects hash into 256 lock files,
so two projects may share one: that can only over-serialize, never allow two committers of
one project at once. A busy lock raises `StoreBusy` after the store's busy timeout and leaves
nothing written.

A receipt is refused unless its schema is `ahl-stage-reuse-v1`, `reused_live_trials` is the
integer 0, `publication_allowed` is `false` and every graph stage is listed exactly once, so
a receipt cannot drop an excluded or unknown node. The caller still supplies the live
checks: reading an output with `store.read(record['output'])` re-hashes it, and the `bytes`
check passes only when that read succeeds.

Not yet implemented: loading the latest committed record of every stage as the planner's
`prior` for the whole graph (`ahl analyze --incremental` does it for process mining only),
the other stages' producers, and a command that commits whole-run receipts.
