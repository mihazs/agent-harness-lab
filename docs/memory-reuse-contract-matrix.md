# Evidence reuse contract matrix

[Why reuse is split this way](memory-reuse-rationale.md) · [Evaluation contracts](evaluation-foundation.md)

This reference records what the repository already enforces for each authoritative
store or reader, so a later persistent-evidence slice adapts to it instead of guessing.
It describes current behavior on `main`; the [private memory store](memory-store.md) provides the persistence layer, and no planner reads it yet.
Every module path below is checked by a test, and the characterization tests in
`tests/unit/docs/test_memory_reuse_contract.py` execute the bounds and projections
quoted here with synthetic data only.

## Reuse classes

| Class | Meaning |
| --- | --- |
| `reusable` | A pure function of identity-pinned inputs. A cached output may replace recomputation when every identity matches. |
| `replayable` | Authority lives in the owning store. A cached value is only a hint and is trusted after the owner's live validation passes. |
| `historical-only` | A readable observation. It informs longitudinal understanding and is never a fresh trial or a new qualified comparison. |

## Matrix

Bounds are the limits visible in code. "Unbounded" means no fixed ceiling, not unlimited resources.

| Store or reader | Identity | Reader or validator API | Bounds | Who may read | Class |
| --- | --- | --- | --- | --- | --- |
| Source records and cache, `agent_harness_lab/sources/store.py`, `agent_harness_lab/sources/schema.py` | `(source_sha256, native_id_sha256, revision_sha256)`. `source_key` hashes kind, provider, root, project and scope; adapter version, auth and enablement do not change it. | `SourceStore.inventory`, `status`, `revisions`, `history`, `capture`, `graph`, `read_snapshot`; writer `ingest` under a file lock with atomic replace | One JSON file read whole. `max_bytes` and `max_records` default to unbounded and are caller-chosen. | Operator process only. Role views exclude it. | `replayable` |
| Source context, `agent_harness_lab/sources/context.py` | Explicit project and scope fingerprints; directory names bind nothing | `load` returns the content-free `inventory()` | Same as the cache | Operator; the summary lands in the private analysis output | `replayable` summary; completeness stays `unknown` |
| Source lineage, `agent_harness_lab/sources/lineage.py` | Case id hashes project, scope, whole-cache SHA-256 and claim | `LineageStore.cases`, `show`, `query`, `unchanged`; `members` re-checks endpoints against the live cache | `max_events`, `max_metadata` and `max_entries` are `None`; `cases()` calls `case_ids(None)` | Operator attestation only; no automatic independence | `replayable`; bound to the whole cache hash |
| Sealed evaluation history, `agent_harness_lab/evaluation/oracle_history.py`, `agent_harness_lab/private_journal.py` | Run id, hash-chained events, `inputs.json` and `evidence.json` pins; store format `experiment-oracle-history-v1` | `HistoryStore.show`, `checked`, `analyze`, `decision`, `verify_decision`, `historical_plan` | 256 case directories including oracle registrations; 64 events, 256 KiB per event, 128 journal entries by default; 64 MiB and 4,096 files of execution evidence | Operator/controller. Roles receive no store path. | `historical-only` for outcomes; authority stays in the store |
| Oracle quarantine, `agent_harness_lab/evaluation/oracle_history.py` | Case id `sha256("oracle:" + bundle)`; monotone `active` to `quarantined` | `HistoryStore.oracle`, `quarantine`; `checked` yields `current`, `invalidated` or `incomplete` | Counts against the 256-case ceiling | Operator | Authority: always read live, never cache |
| Bank authority, `agent_harness_lab/evaluation/oracle_history.py` | Bank hash, revision hash, per-task revision, materialized and execution fixture hashes | `bank_authority(source, expected=...)` re-validates the live bank | 1,000 tasks, 4,096 files, 64 MiB | Operator | Authority: always read live |
| Construction-access store selection, `agent_harness_lab/evaluation/construction_access.py` | Store identity is a hash of canonical path, device and inode | `selection`, `store_identity`, `current_store_identity`, `WorkflowAccess` | The store must be disjoint from project, output, skills, bank, capture, image, settings and credentials | Controller process; handoffs are one-use and process-owned | `historical-only`; a serialized receipt is not custody |
| Protected diagnostic access, `agent_harness_lab/evaluation/certification_access.py` | Binding over manifest, plan, input trees, oracle, runtime and engine hashes | `HistoryStore.reserve_access`, `access_ledger` | 1 MiB per record | Operator | `historical-only`; one-shot, never reusable |
| Frozen plans, `agent_harness_lab/evaluation/plan.py` | `ExperimentSpec.digest` (SHA-256 of the sorted validated declaration) | `ExperimentSpec.parse`, `validate`, `schedule` | `max_trials`, identity strings up to 128 characters | Anyone holding the manifest | `reusable`; schedule is seeded and deterministic |
| Statistics, `agent_harness_lab/evaluation/statistics.py` | Plan digest plus ordered `TrialRecord` identities | `analyze`, `bound` | Pure computation | Operator | `reusable` for a fixed plan digest, results hash and engine |
| Decision receipts, `agent_harness_lab/evaluation/decision_receipts.py` | Receipt SHA-256; `engine()` hashes every package source file plus interpreter identity | `create`, `verify` recompute from live authority | 2 MiB per receipt, 16 files | Operator | `replayable`; any code change makes it stale |
| Preservation cohorts, `agent_harness_lab/evaluation/cohorts.py`, `agent_harness_lab/evaluation/cohort_history.py`, `agent_harness_lab/evaluation/preservation_analysis.py` | Manifest SHA-256 and qualification bindings to prior sealed runs | `capture`, `validate_snapshot`, `revalidate` | 1 MiB manifest; qualification depth at most 8 | Operator | `replayable`; membership is `current_membership_only` |
| Process mining, `agent_harness_lab/diagnostics/process_mining.py`, `tools/diagnose.py` | Profile strings `ahl-private-diagnosis-v1`, `ahl-analysis-execution-v1`, `ahl-analysis-process-graph-v1` | `ProcessMiner`, `analyze` | Streaming; optional `max_events`; event bodies are not retained | Operator | `reusable`; descriptive only |
| Resource decisions, `agent_harness_lab/evaluation/resource_decision.py`, `agent_harness_lab/evaluation/resource_history.py` | Contract SHA-256 and profile `resource-noninferiority-v1` to `v4` | `validate`, `decide`; sealed evidence through `resource_history.validate` | 64 token endpoints, 1,000 token requests | Operator | `reusable` as a pure function; unknown never becomes zero |
| Role projection, `agent_harness_lab/evaluation/construction.py` | Fixed allow-list of summary keys | `views` | Allow-listed keys only; two optional historical keys, `incident_retrieval` and `paired_history_analysis`, are copied whole | Proposer and evaluator-author roles | See finding 4 |
| Event envelope, `agent_harness_lab/telemetry/schema.py` | `schema_version` 1 or 2 | `event` | Unlisted fields are dropped; listed text fields are kept as given | Operator | `reusable`; content-free by field list, not by value |

## Findings that settle the open questions

1. **Analysis stages.** `agent_harness_lab/evidence.py` `collect` streams events through one
   pass of `tools/diagnose.py` `analyze`. Ordering flags, process mining, sequence and
   observation matching share that pass, so they are not separately cacheable today.
   Separable deterministic units are the whole collect pass (its `fingerprint` already hashes
   the normalized event stream and evaluation identities), `agent_harness_lab/analysis_receipt.py`
   `summarize` and report rendering. In `improve`, the proposer and author roles, fresh trials
   and grading are not deterministic and are never reusable. Stage-level algorithm versions
   do not exist; only output profile strings and the package-wide `engine()` hash do.
2. **Freshness attestation.** No adapter attests recency into the cache. Local exports and
   Git derive a snapshot identity from exported content, which detects change but not age.
   The GitHub adapter keeps `ETag` validators in memory for one adapter instance only, and
   they are never persisted. Cross-source joins report `native_freshness_verified` as
   false, and memory-page capture reports snapshot atomicity as unavailable. Declared
   coverage and knowledge-page `stale_after` values are author claims. Remote freshness is
   therefore `unknown`.
3. **Per-family longitudinal views.** `agent_harness_lab/evaluation/history_report.py`
   `task_pairs` yields task, root, family and repetition rows from sealed receipts, with
   outcome, grader identity and runtime image digest, and marks pairs `not comparable`
   when grader or runtime differ. Identities, hashes and outcome booleans suffice, so no
   sensitive artifact needs copying. The gaps: reports need explicit run and receipt
   references, `HistoryStore` has no list or search action, and model identity is proven
   only as an inference-settings hash for schema versions 6 to 8.
4. **Role projection.** `construction.views` copies a fixed key list. Beyond aggregate
   analysis counts, it passes two optional historical items verbatim to both roles in
   `analysis-summary.json`. `paired_history_analysis` is one explicitly selected result,
   which `HistoryStore.analyze(read_only=True)` limits to fixed schema-1 or bank-bound schema-2
   history without protected access or derivation. `incident_retrieval` is the operator-supplied reviewed-incident
   search result from `agent_harness_lab/diagnostics/retrieval.py` `contextual_search`; it
   carries incident ids, summaries, evidence references and scores. Its approval is a caller
   declaration (`caller_declared_reviewed_approval_unverified`), each row is marked
   `untrusted_evidence_only`, and the summary's own limitations say so. Source context,
   evaluation outcomes and per-finding evidence are not projected. Any other historical
   finding would require a new reviewed key.
5. **Legacy enumeration and capacity.** `PrivateJournal.case_ids(maximum)` is the only
   enumeration. It returns sorted ids without separating oracle cases from run cases and
   raises when `maximum` is exceeded; `case_ids(None)` removes only that bound.
   `HistoryStore.new_case` refuses the 257th case directory.
6. **Whole-cache lineage binding.** Lineage review identity includes the whole cache file
   hash, so ingesting any record makes earlier reviews non-matching.
7. **Disjointness.** `construction_access._disjoint` lists the inputs an evaluation store
   must stay outside. A future memory store root must be added to that list so role
   inputs can never contain it.
8. **No secret scanning.** The repository has no redaction or secret scanner. The event
   envelope removes unlisted fields but keeps listed text fields verbatim.

## Fixtures and expected decisions

`tests/support/memory_reuse_fixtures.json` holds synthetic scenarios only. Each declares
changes from one baseline and the expected decision per stage and the claim a consumer may
make. Decisions: `reuse`, `recompute`, `verify_then_replay`, `not_comparable`, `blocked`,
`incomplete`, `legacy_unverified`, `not_reusable`, `excluded`. Claims:
`historical_observation_only` and `fresh_qualified_evaluation_required`.

| Fixture | Issue case | Deterministic stages | Sealed comparison | Claim |
| --- | --- | --- | --- | --- |
| `unchanged_rerun` | Unchanged re-run | All `reuse` | `verify_then_replay` | historical only |
| `changed_source_revision` | One changed source revision | All `recompute` | `verify_then_replay` | historical only |
| `changed_parser` | Changed parser | Capture `reuse`, rest `recompute` | `verify_then_replay` | historical only |
| `changed_algorithm` | Changed algorithm | Analysis and summary `recompute` | `verify_then_replay` | historical only |
| `changed_grader_hash` | Changed grader hash | All `reuse` | `not_comparable` | fresh evaluation |
| `quarantined_oracle` | Quarantined oracle | All `reuse` | `blocked` | fresh evaluation |
| `changed_model_runtime` | Changed model or runtime | All `reuse` | `not_comparable` | fresh evaluation |
| `legacy_record_missing_provenance` | Legacy record, missing provenance | All `reuse` | `legacy_unverified` | fresh evaluation |
| `interrupted_run` | Interrupted run | All `reuse` | `incomplete` | fresh evaluation |
| `secret_bearing_trace` | Secret-bearing trace | Capture `excluded`, rest `blocked` | `verify_then_replay` | historical only |

`fresh_trial` is `not_reusable` in every fixture. The secret fixture contains one
declared synthetic canary and nothing resembling a real credential.

## Legacy compatibility

| Record | What is missing or unknown | Label | Permitted use |
| --- | --- | --- | --- |
| `HistoryStore` run, input schema 1 | Bank authority, behavior policy and cohort binding | `legacy_unverified` | Read-only descriptive history; never compared with bound runs |
| Input schemas 2 to 5 | Model inference (offline agent) | Comparable only on identical grader, bank, plan and runtime | Historical longitudinal observation |
| Input schemas 6 to 8 | Model identity beyond the settings hash | Comparable only on identical settings hash, grader, bank and runtime | Historical observation |
| Unsealed run | Seal and results | `incomplete` | None; never resumed |
| Run depending on a quarantined bundle | Oracle validity | `invalidated`, reported as `blocked` | Audit trail only |
| Any pair with unknown grader or runtime | Identity | `not_comparable` | Display only |
| Source record schema 1 | Structured outcome | Outcome `unknown` | Descriptive only |
| Source record with unknown completeness or independence | Coverage, lineage | `unknown` stays `unknown` | Never counted as zero, success or independence |

## Open product decisions with recommended defaults

| Decision | Recommended default |
| --- | --- |
| Opt-in per project or a default location | Local, explicit opt-in; no inferred global location |
| Components stable enough for cross-run reuse | Only deterministic stages in the matrix marked `reusable`; authority stages stay live |
| Adapter freshness attestations | Content identity only; recency is `unknown` until an adapter proves it |
| Metrics and margins | Descriptive only until a frozen, qualified plan declares them |
| Raw trace retention | Derived summaries only; raw bodies excluded by default |
| Evidence crossing role boundaries | None beyond the current construction-safe development evidence |
| New behavioral degradation claim | A fresh qualified evaluation is required |
| Knowledge wiki | Non-authoritative navigation layer |
