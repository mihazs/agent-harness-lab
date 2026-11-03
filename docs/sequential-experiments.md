# Conditional finite-bank sequential experiments

This opt-in profile executes fresh root blocks through the existing `ahl evaluate`
controller or `ahl experiment-history run`. It may stop before unobserved roots
when the declared finite-bank criteria are supported or a harm boundary is crossed.
It is private diagnostic evidence, not certification, adoption or proof of future
model quality. The fixed-final profile remains the default.

Add this explicit object to a frozen experiment plan:

```json
{
  "sequential": {
    "schema_version": 1,
    "method": "wor-hoeffding-predictable-rational-v1",
    "estimand": "finite_bank_realized_potential_root_block_mean",
    "assumptions": [
      "root_potential_outcomes_independent_of_controller_permutation",
      "no_cross_root_interference_or_execution_order_effects",
      "frozen_root_local_protocol"
    ],
    "max_roots": 20,
    "root_seconds": 30,
    "arm_seconds": 5
  }
}
```

This example requires at least20 declared roots. The root quota must exceed the
sum of both executor and fresh-grader arm quotas for every task/repeat in the
largest root, leaving time for setup. All quotas and observations share the
original parent deadline and resource ceilings. Grader preparation consumes its
fixed arm budget; the kernel execution receives the remainder, not a reset budget. `max_trials` must cover the
largest possible selected `max_roots` blocks. There are at most1000 tasks and
10000 scheduled arm trials. Fixed `precision_half_width` targets, protected
partitions, cooperative native CLIs and unsupported consumer combinations reject;
the fixed precision command cannot admit a sequential design.

The controller draws a fresh uniform root permutation using unbiased system-random
Fisher–Yates draws, records it before scoring, and freezes actual positive tuning
values as exact rational numbers. A user seed controls root-local AB/BA scheduling;
it does not establish uniform root sampling. Every root-local task/repetition/arm
order is independent of the sampled root position. Each observed root finishes
both arms and all its repetitions before one statistical update. Unknown or
incomplete roots stop execution; they are not skipped or replaced.

The target is the finite bank's realized potential root-block mean under the
frozen protocol. Conditional on a root-indexed potential-outcome vector independent
of sampling order, the without-replacement confidence sequence applies even if
that vector is stochastic. This assumes no interference or order-dependent effects.
Those scientific assumptions remain **declared and unverified**. Pinned images,
matching configuration and isolated workspaces do not authenticate stationarity,
model randomness, semantic root independence or deterministic output. Existing
input pins retain their recorded byte/mode coverage; unrecorded filesystem modes
and provider identity are not upgraded by this profile. Observed
input/configuration/order/coverage violations prevent a valid favorable stop.
No expected future-task or expected-provider-performance claim follows.

Root paired means, remaining-root bounds, normalized canonical-decimal family
weights and margin comparisons use exact rational arithmetic. The published
predictable Hoeffding WoR construction supplies a time-uniform interval; a
conservative outward logarithm bound avoids floating-point equality upgrades.
Family and required-cohort channels share Bonferroni alpha. The primary interval
is their frozen weighted family sum. Cohort root means use only frozen member
tasks, including when they differ from the whole-root mean. Related tasks and
repeated trials remain one root. Empty running intersections stay explicit
inconsistency/inconclusive; they are never sorted, reset or reseeded to recover a
positive decision. A census identifies the realized bank mean, not future model
success probability.

Benefit requires a strict primary practical-effect crossing and every required
family/cohort NI and minimum-root condition. Observed candidate critical effects
veto immediately, including in a partial root, without inserting an incomplete
block into the mean. Budget/root ceilings give inconclusive results; missing arms,
ambiguous requests and lost evidence remain incomplete. Unseen harms stay unknown.
All outputs retain `automatic_promotion=false` and `publication_allowed=false`.

Sampling, each started root and each inspected boundary are bounded private
immutable records. Controller history schema5 binds these records and rechecks
actual isolated worker/fresh-grader evidence, current oracle/bank/cohort authority,
and the analysis engine before guarded analysis or receipts. Legacy history
schemas retain their full-schedule rules. A saved JSON result or checksum does not
recreate a live sampling controller. Cold execution resume is unsupported: an
in-flight attempt is retained, not silently re-executed. Starting another run
executes fresh calls with distinct sampling/trial custody and consumes new budget;
sequential stopping does not authorize repeated candidate selection on final data.

Guarded optimization and full-schedule correction/requalification inputs currently
reject this new profile rather than treating stopped prefixes as completed banks.
Use guarded history analysis/decision receipts for current private evidence. Raw
`experiment --records` imports cannot authenticate a sequential stop.

Method: [Waudby-Smith and Ramdas2020, Theorem3.1](https://proceedings.neurips.cc/paper_files/paper/2020/file/e96c7de8f6390b1e6c71556e4e0a4959-Paper.pdf),
using a frozen predictable tuning table; see
[Howard et al.2021](https://doi.org/10.1214/20-AOS1991) for time-uniform inference.
No live provider/pilot or cost-saving measurement is implied. Full issue9 remains
open for remaining orchestration, validation and certification dependencies.
