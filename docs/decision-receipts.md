# Private controller decision receipts

A decision receipt binds an analysis to an actual sealed controller comparison.
It records evidence about that run. It does not recommend or automatically adopt
a candidate, establish native reviewer identity, or make a development cohort
eligible for certification.

Create a receipt from an explicit private controller history:

```sh
python3 -m agent_harness_lab experiment-history --store /private/history decision --run RUN_SHA256
```

The command returns exit code 3, like `analyze`: successfully saving analysis is
not a favorable promotion decision. Retain its `receipt_sha256` with the private
result. Verify that exact receipt against current authority:

```sh
python3 -m agent_harness_lab experiment-history --store /private/history verify-decision --run RUN_SHA256 --receipt-sha256 RECEIPT_SHA256
```

A successful verification returns exit code 0 for current evidence. The receipt's
analysis and endpoint dispositions still determine whether the comparison is
supported, rejected, blocked, incomplete or inconclusive. Both commands retain
`publication_allowed: false` and `automatic_promotion: false`.

Python callers use `HistoryStore.decision(run_id)` and
`HistoryStore.verify_decision(run_id, receipt_sha256)`. A copied return value or
callback claiming `safe`, `supported` or `current_recomputed` is not authority.
Consumers must invoke verification against the actual store and exact receipt.

Only sealed, currently valid executions can produce a receipt. The controller
binds the plan, observed execution and evidence, baseline/candidate identities,
oracle dependencies and analysis. Cohort-enabled runs also bind frozen manifest
bytes, qualification evidence, endpoint dispositions and missing observations.
The saved receipt is bounded and immutable, outside the sealed execution tree.
Recreating the same receipt is idempotent.

Each run retains at most 16 receipts and 16 MiB of sidecars; each sidecar is
limited to 2 MiB and the recomputed analysis to 16 MiB. Unexpected files,
interrupted writes or altered receipts block verification until reviewed. These
limits do not alter the original execution seal or remove historical evidence.

Verification reopens the current bank, oracle and qualification authorities and
recomputes analysis. Revoked authority, altered saved evidence or a different
analysis engine invalidates the old receipt. Python implementation/version,
application Python sources and the evaluator form part of that engine identity;
regenerate a receipt explicitly
after an engine change rather than interpreting an old numeric result as current.
Old receipts remain historical evidence and cannot restore revoked eligibility.

Unbound legacy or exposed development runs can retain descriptive or negative
analysis receipts. Missing authority stays unverified. Receipt creation cannot
upgrade their exposure role or authorize certification.

Keep all receipts private. They include controller evidence and provenance;
synthetic-looking labels and publication claims cannot authorize public export.
[Private history reports](history-reports.md) resolve current receipts and retain
qualification provenance, preservation intervals and unknowns. [Guarded development
search](guarded-search.md) consumes fresh controller receipts. Corrected-oracle
requalification remains separate work in issue #122.
Cohort-enabled promotion remains blocked until integration.

## Default improve single-run dispositions

Every `ahl improve` evaluation that does not use a fixed repeated-bank request
runs each task once per arm. This includes the default path and a configured
pinned project bank. When such an evaluation has exact, unique baseline/candidate
coverage, the controller adds a `decision` block with profile
`ahl-single-run-decision-disposition-v1` to `workflow.json`. It explains why that
evidence cannot support a recommendation. It is not a sealed controller receipt
and does not replace the `recommendation` field, status or exit code, which keep
their previous values.

- `disposition` is `insufficient_evidence`, or `invalid_evidence` when frozen
  inputs changed. `next_evidence` is `requires_repeated_paired_experiment`.
- `observed_pattern` labels what one run showed, such as
  `candidate_passed_where_baseline_failed`, `difference_outside_gate_splits`
  (only held-out or undeclared splits differ), `no_observed_difference`,
  `candidate_failures_observed` or `unknown_trial_outcomes`. Pair counts per
  manifest split separate `candidate_only_passed`, `baseline_only_passed`,
  `both_passed`, `both_failed` and `unknown`. Statuses other than pass/fail, and
  trials whose reported split disagrees with the manifest, stay unknown.
- `reasons` lists the unmet prerequisites: one repeat per task and arm, no frozen
  experiment plan, a descriptive profile, undeclared or unverified independent
  roots, isolation qualification from the live prerequisite scope that is not
  `qualified`, and each unverified mandatory certification prerequisite.
- `promotion` mirrors the fail-closed policy outcome (`inconclusive` with
  `descriptive_smoke_cannot_certify`, or `blocked`). `bindings` carry the
  attempt, evaluation directory, manifest and `results.json` hashes, and the
  baseline and candidate hashes of the latest covered attempt. `final_attempt`
  and `describes_final_attempt` state whether a later attempt began after it.
  An unreadable, irregular or oversized `results.json` binds as `unknown`; the
  trial workspaces are not hashed.

A candidate whose paired score fell below its baseline additionally ends `blocked` with reason
`candidate_regressed`; the [before/after guard](before-after-guard.md) records the paired delta, the
frozen inputs of both arms and an investigation. The `decision` block above is unchanged.

Every authority field stays false. A run without an exactly covered evaluation
carries no block. If the explanation cannot be built, the block is
`{"status": "unavailable"}` with the error type, and the run result is unchanged.
The opt-in [repeated-bank workflow](repeated-improvement.md) keeps its own
verified receipt view under the same key. Public benchmark export rejects the
profile.

Use `task test:evaluation:receipts` for the focused native receipt and CLI cases,
and `task test:evaluation:preservation` for qualification and analysis contracts.
These tests do not establish live model performance or
semantic independence of original roots.
