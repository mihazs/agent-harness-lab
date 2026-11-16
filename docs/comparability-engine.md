# Comparability engine

[Private memory store](memory-store.md) · [Evidence reuse contract matrix](memory-reuse-contract-matrix.md) · [Why reuse is split by authority](memory-reuse-rationale.md) · [Stage reuse planner](stage-reuse-planner.md)

Reference for `agent_harness_lab.memory.comparability`, the pure engine that decides whether two
evaluations may be compared for a behavioral claim, and names every gate that stops them. It is a
library module in this release: it reads no store, calls no model, has no CLI and authenticates
nothing. The caller supplies the facts, taken from frozen plans, identities and live authority
checks such as the [history index](memory-store.md#list-existing-evaluation-histories); the
engine only decides. Facts are therefore reported as `caller_supplied_unverified`.

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `comparable` | Every gate below passes on both sides. This only makes a regression claim *eligible*; it proves no difference |
| `not_comparable` | At least one stratum gate has a known value on both sides and the values differ. The evaluations measure different things and belong to separate strata |
| `insufficient_evidence` | No stratum difference is known, but a gate is unknown, invalid, stale or short of its minimum. Nothing can be compared yet |

`not_comparable` wins when both occur, and the degraded gates are still listed. The result is
`ahl-comparability-v1`: `verdict`, `failures` (`gate` and `reason: differs`),
`evidence_degradation` (`gate`, `side` and a reason of `unknown`, `invalid` or `insufficient`),
`stratum` (a digest per side, `null` when unknown), `declared_roots`, `independence: unverified`,
`regression_claim_eligible`, `facts_authority` and `publication_allowed: false`. Evidence degradation
is reported apart from `failures`, so a quarantined oracle or a coverage gap is never read as a
change in the agent's behavior. `regression_claim_eligible` is true only for `comparable`; the claim
ladder and the statistics belong to the later degradation report.

## Gates

A value is *unknown* when it is missing, `null`, an empty string, list or object, non-finite,
not JSON-serializable, nested deeper than 8 levels, longer than 1 MiB as canonical JSON, or
contains any such part. Unknown never
matches anything, including another unknown. Values compare by canonical JSON, so key order is
irrelevant and `true` differs from `1` and `1` from `1.0`.

| Kind | Gate | The caller supplies |
| --- | --- | --- |
| Stratum | `endpoint` | Profile, precision target and sequential declaration of the frozen interpretation |
| Stratum | `error_control` | Alpha, effect and family margins, family weights (multiplicity) |
| Stratum | `task_definitions` | Task, root and family triples |
| Stratum | `partition_policy` | Partition and cohort manifest |
| Stratum | `grader_identity` | The grader or oracle identity digest |
| Stratum | `task_selection` | Bank and revision digests and the exact task and fixture selection |
| Stratum | `baseline_identity` | The qualified baseline harness identity, for example its [snapshot id](harness-snapshots.md). A different reference is a different stratum; a blocked snapshot has no id and is unknown |
| Stratum | `design` | Design kind, repetition count and sequential declaration |
| Stratum | `runtime` | The controlled runtime or environment identity |
| Stratum | `model_settings` | Model, provider and effective execution settings, for example the inference-settings hash |
| Stratum | `metrics` | The metric contract and denominators |
| Stratum | `resource_boundaries` | Resource contract, timing, token and cache boundaries |
| Stratum | `missingness` | How unknown outcomes are counted |
| Known | `candidate_identity` | The qualified candidate identity, for example its snapshot id; it may differ between evaluations |
| Known | `trial_lineage` | The pinned realized assignment of the executed trials; it may differ |
| Current | `oracle_authority` | `True` while no dependent oracle is quarantined |
| Current | `bank_eligibility` | `True` while the bank authority is current and eligible |
| Current | `evidence_current` | `True` while the sealed evidence still validates |
| Current | `held_out_integrity` | `True` when no held-out or protected access contaminated the run |
| Current | `fresh_execution` | `True` when no old trial is reused as a new execution |
| Sufficiency | `independent_roots` | `roots` (one root id per trial) and the frozen `min_roots` (at least 2) |

A *Current* gate passes only on the boolean `True`. `False` is reported as `invalid`, anything else
as `unknown`. An uncontrolled environment has no runtime identity, so `runtime` is unknown and the
evaluation has no stratum.

Repeated trials of one root count once: `declared_roots` is the number of distinct root ids, so
repetitions never raise it, and fewer roots than `min_roots` is `insufficient`. A root id is a
non-empty string of at most 128 characters, the experiment plan's identity bound; any other root
fact makes the count unknown. The count is the
plan's declaration, never verified independence, and the result says `independence: unverified`.

A predeclared supported bridge between differing runtimes or models is not implemented; such a
difference is a separate stratum. An A/A check, or any key outside this table, is ignored: it can
diagnose instability but never makes history comparable.

## Strata

`stratum(facts)` is the SHA-256 of the canonical values of every stratum gate, or `None` when any is
unknown. Two evaluations share a stratum exactly when no stratum gate differs. `group_strata`
groups named evaluations by that digest and lists those with an unknown stratum gate under
`unassigned` instead of merging them. Grouping describes definitions only; a `Current` or `Known`
gate does not change a stratum.

Not yet implemented: deriving the facts from a sealed history, a harness snapshot and a metric
contract (each gate is to be mapped to its recorded source, and a gate with none stays unknown), a
command that runs the engine, and the degradation report that consumes the verdict.
