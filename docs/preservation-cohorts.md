# Frozen preservation qualification and conditional analysis

A preservation cohort identifies behavior that worked in a prior execution and
must be evaluated separately from aggregate gains. Frozen membership and resolved
prior qualification feed the existing paired-root analysis. Each required cohort
gets its own noninferiority endpoint. [Private decision receipts](decision-receipts.md)
bind the analysis to current evidence; optimization/report integration remains
pending. Conditional statistical support cannot authorize adoption.

Existing plans keep their original serialization, digest and scheduled trial IDs.
An optional `cohort_manifest_sha256` field binds the exact bytes of a new sidecar.
The evaluation manifest declares its relative path and matching SHA-256:

```json
{
  "cohort_manifest": {
    "path": "cohorts.json",
    "sha256": "REPLACE_WITH_THE_EXACT_SIDECAR_SHA256"
  },
  "experiment": {
    "cohort_manifest_sha256": "REPLACE_WITH_THE_SAME_SHA256"
  }
}
```

This fragment extends the complete existing evaluation manifest and experiment
specification. Place the sidecar inside the explicit export directory. Keep it
private: it contains controller history paths, reviewer attribution and task pins.

The sidecar schema is `schema_version: 1`, `profile: preservation-cohorts-v1`,
an `author`, and a nonempty `cohorts` list. Each cohort has:

- A unique `cohort_id`, role `historical_pass` or `previously_repaired`, boolean
  `required`, predeclared noninferiority `margin`, and `exposure: development_regression`.
- `severity_policy: verified_critical_veto`, preserving the existing separate veto
  for verified critical-invariant violations, including optional reporting cohorts.
- `qualification` containing the source `history_store`, `run_id`,
  `event_sha256`, `evidence_sha256`, `plan_sha256`, selected `arm`, a frozen
  `rule` with positive integer `min_trials` and `min_successes`, and `review`.
- Review fields `identity`, `source: operator_attestation`,
  `basis_kind: independent_artifact_review`, and nonempty `evidence_refs`.
  The reviewer differs from the declared author. Native reviewer identity remains
  unverified; attribution alone cannot qualify membership.
- Unique `members`, each binding `task_id`, `root_id`, `family`, `bank_sha256`,
  `bank_revision_sha256`, `task_revision_sha256`, `fixture_sha256`,
  `materialized_sha256`, `execution_fixture_sha256`, `bundle_sha256`, and `grader_version`.

`evaluation.cohorts.capture(path, expected_sha256, planned_tasks, baseline_hash)`
resolves the actual prior controller history and returns a private binding.
`revalidate` checks current authority again; `validate_snapshot` validates retained
historical bytes without approving current eligibility. These functions create no
model calls, bank revisions, acceptance events or public results.

Qualification requires sealed, current bank-bound history with fixture modes,
complete fresh paired executions and valid typed grader output. The selected prior
arm must have the current baseline's harness digest and meet the frozen count rule.
`previously_repaired` also requires an observed baseline-failure/candidate-pass pair
in that prior comparison. This is evidence about that execution, not proof of a
native historical incident or stable stochastic success.

The current evaluation must retain the exact member task, fixture and oracle
contracts. A newer bank revision may retain unchanged members; it cannot substitute
different grading dependencies. Members may appear in multiple cohorts. Primary
tasks remain unique and overlap creates no additional independent roots.
Exposed preservation controls remain development evidence, and cannot be relabelled
untouched transfer or certification tasks. Unbound legacy runs cannot supply missing
fixture-mode authority.

The ordinary `experiment-history run` workflow captures qualification before worker
dispatch, stores `cohort-manifest.json` and `cohort-binding.json` in its private case,
and binds them through history inputs schema 4 and the `run_started` event.
Its `recorded_at` provides the controller freeze time. Guarded reads revalidate prior
oracle and live bank authority. Removing the old export does not remove the retained
snapshot; revocation blocks current use while `historical-plan` retains the original
declarations with current applicability unevaluated.

An oracle correction needs fresh qualification and a newly frozen cohort manifest,
followed by both comparison arms under a new plan. The existing correction-rerun
operation refuses cohort-enabled plans rather than silently retaining old qualification.
Automatic requalification and optimization/report receipt integration remain work
for issue #122. Automatic promotion and publication remain disabled.

## Conditional preservation analysis

`statistics.analyze(spec, records, cohort_binding=...)` requires the exact raw
sidecar bytes as `manifest_json` in addition to the retained binding. Their SHA-256
must match both the plan pin and `manifest_sha256`; the strictly parsed bytes must
match the embedded `manifest`. Changing criteria requires a new plan and trial IDs.
This numeric function validates the declaration but
does not resolve current qualification authority. Private history supplies its
retained binding only after the existing live bank/oracle/qualification checks.
Raw callback booleans or a detached numeric report cannot establish that authority.

For each required cohort, candidate-minus-baseline outcomes are averaged over its
task/repetition pairs within each original root, then over equally weighted roots.
Related variants and repeats remain within their original root. A root appearing
in multiple cohorts remains the same root; overlap creates correlated endpoints.

The existing weighted-root Hoeffding method uses Bonferroni allocation
`alpha / (1 + frozen families + required cohorts)` for primary, family and required
cohort intervals. This allocation permits dependence between endpoints. Its
independent-root and fixed-membership assumptions remain explicit and unverified;
root labels do not establish independence. Optional reporting cohorts receive
descriptive counts without additional confidence claims.

A required cohort is supported when its lower bound meets its frozen `-margin`;
an upper bound below that boundary rejects. Insufficient precision is inconclusive.
Every required endpoint must be supported alongside the original primary/family
criteria before conditional support. Missing or invalid planned evidence stays
incomplete or blocked; verified critical-invariant violations retain their veto.

Reports retain pass/pass, fail/pass, pass/fail, fail/fail, missing, invalid and
unknown paired outcomes, plus task/root rates and planned denominators. A rate is
unknown when required observations are missing. One stochastic pass-to-fail pair
is descriptive evidence, not proof of a stable regression. Reports preserve these
counts before early negative decisions and remain private.

Promotion of cohort-enabled plans remains blocked while optimization/report
integration and corrected-oracle requalification are pending.
Run `task test:evaluation:preservation-analysis` for the synthetic aggregate-gain
counterexample, powered preservation, sparse data, overlap, missing outcomes,
selection-bias construction and legacy compatibility checks.

Run `task test:evaluation:preservation` for native synthetic qualification, identity,
revocation and history checks. Isolation attestations in these unit fixtures are
simulated. They establish controller contracts, not kernel containment, model gains
or semantic independence.
`task test:sandbox:kernel:preservation` separately exercises actual pinned offline
workers and fresh graders for prior qualification and a new matched comparison.
It still leaves native reviewer identity and semantic independence unverified.
