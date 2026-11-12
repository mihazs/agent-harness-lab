# Stage reuse planner

[Private memory store](memory-store.md) · [Evidence reuse contract matrix](memory-reuse-contract-matrix.md) · [Why reuse is split by authority](memory-reuse-rationale.md)

Reference for `agent_harness_lab.memory.reuse`, the pure planner that decides, per
deterministic stage, whether a prior output may replace recomputation, and explains
every hit and miss with a typed reason. It is a library module in this release: it reads
no store, calls no model, has no CLI and authenticates nothing. The caller supplies the
prior records and the results of live authority checks; the planner only decides.

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
| `miss_input_changed` | An input digest changed, directly or through an upstream stage |
| `miss_algorithm_changed` | Stage version, config or canonicalizer changed |
| `miss_policy_changed` | Policy changed |
| `miss_scope_mismatch` | Scope changed |
| `miss_freshness_unknown` | Freshness was not verified; a TTL never counts. Reported as unknown |
| `invalid_integrity` | Stored key or output, or the bytes, failed verification |
| `invalid_oracle_quarantine` | Live oracle check failed (`deterministic_grade`) |
| `invalid_bank_authority` | Live bank check failed (`deterministic_grade`) |
| `blocked_role_access` | Inputs are not authorized for the role; dependents are excluded too |
| `nonreplayable_live_trial` | Live trials and proposals are historical evidence only |

A live check passes only on `True`. A missing result is unknown and listed under
`unknown`, never as a pass. Changing a grader hash (carried in config) misses
`deterministic_grade` and its dependents while the upstream capture still hits, so raw
evidence is retained but prior scoring is not silently re-authorized.

## Receipt

`ahl-stage-reuse-v1` is a dictionary with `reused`, `executed`, `excluded` and `unknown`
lists (each row: `stage`, `reason`, `node_key`, and `output` when reused), plus
`excluded_historical`, `counts` of reused and executed deterministic stages,
`reused_live_trials: 0`, `source_freshness` (`verified` or `unknown`) and
`publication_allowed: false`. Every graph stage appears exactly once.

Not yet implemented: committing a receipt or stage output into the store under its exact
key with a bounded per-project lock, and a command that runs the planner.
