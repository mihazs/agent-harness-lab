# Frozen drift plans and receipts

`agent_harness_lab.diagnostics.drift` keeps three kinds of change apart: a broken
capture path, a different task population and a change in verified outcomes. This
first slice of issue #76 is a library API. It freezes the monitoring plan, freezes a
reference as counts, and returns a receipt for one later window. It adds no CLI
command and no background monitor, and it does not change `promotion/monitor.py`.

## Plan

`check_plan(plan)` validates a `drift-plan-v1` and returns its SHA-256. Freeze it
before reading any monitored window.

```json
{"schema_version":1,"profile":"drift-plan-v1","stream_sha256":"<sha256>",
 "reference_revisions_sha256":["<sha256>"],"source_schema_version":1,"unit":"task_root",
 "strata":["task_family","effective_model_version"],"features":["repo_size"],
 "required_fields":["task_family","effective_model_version"],
 "capture":{"min_coverage":C,"max_coverage_drop":D},"method":"fixed_window",
 "reference_window":[S,E],"window_length":L,"max_windows":K,"min_exposure":N,
 "effect_size":{"task_mix_total_variation":T,"failure_rate_increase":F},
 "alert_budget":{"task_mix":A,"performance":B},
 "multiplicity":"bonferroni_windows_and_strata","reset_policy":"new_plan_retains_history",
 "action":"observe"}
```

The letters are placeholders. AHL supplies no default coverage rule, exposure,
effect size or alert budget. The plan stores hashes and approved metadata only.

- Strata come from `task_family`, `runtime` and `effective_model_version`, and each
  stratum field must also be a required field. `requested_model` is refused as a
  stratum or feature: a requested alias is not the version that ran.
- Features name observable task fields. Run metadata (`outcome`, `cursor`,
  `harness_revision` and the rest) cannot be a feature.
- Window `k` covers `[E + k*L, E + (k+1)*L)`. Windows never overlap. A window index
  outside `0..K-1` is refused, so a reset needs a new plan that keeps the history
  and the error already spent. Restarting a process does not reset the budget.
- Each statistical channel's budget is split evenly over the `K` windows. The
  receipt reports this as `per_window_alpha`.

## Runs

Each run is a dict with `run_id`, `task_root`, `cursor` (an integer sequence),
`started`, `schema_version`, `capture` (`complete`, `partial` or `unknown`),
`truncated`, `parse_rejected`, `harness_revision`, `outcome` (`true`, `false` or
`null`), `outcome_provenance`, and the stratum and feature fields. Runs may be a
generator. Use synthetic or reviewed private runs only.

A run is eligible only when its capture is `complete`, its schema version matches,
it is neither truncated nor parse-rejected, every required field is present, and
its task root and harness revision are known. In the reference window its revision
must also be one of `reference_revisions_sha256`. An ineligible run lowers capture
coverage. It never enters the task-mix or performance counts, so partial or unknown
capture cannot appear as a performance change.

Repeated runs of one task root (retries) count as one observation. A root is
counted as unknown and stays out of the performance denominator when any of its
eligible runs has a missing label (`missing_label_roots`), otherwise when any
outcome came from provenance other than `executable_grader` or
`independent_review` (`unverified_roots`), otherwise when its verified outcomes
disagree (`mixed_outcome_roots`). A verified run never hides an unlabelled or
unverified retry. A root whose stratum values are empty or inconsistent is counted
as an unknown stratum, never as a precise one.
Each stratum and feature keeps at most 64 categories; the rest are counted as
`other`. The performance cells use the same bounded stratum keys as the task mix.

## Reference and receipt

`freeze_reference(plan, runs)` returns a `drift-reference-v1`: counts for the
reference window, with root and run IDs reduced to hashes that are not stored. It
refuses a reference window that has a capture alert or fewer eligible task roots
than `min_exposure`. `assess(plan, reference, runs, window_index)` checks the plan
hash and the reference digest, then returns a `drift-receipt-v1`.

| Channel | What it reports | States in this slice |
| --- | --- | --- |
| `capture` | Ineligible runs by reason, coverage against the reference, identical duplicates suppressed, conflicting duplicates, unknown cursors, cursor gaps and collisions in the window, untimed runs | `alert`, `no_alert`, `insufficient_evidence` |
| `task_mix` | Eligible task roots per stratum and feature value in the reference and current window, new and absent strata, unknown strata | `insufficient_evidence` |
| `performance` | Verified roots and failures per stratum on both sides, whether each stratum has `min_exposure` on both sides, and missing-label, unverified and mixed-outcome roots | `insufficient_evidence` |

The capture channel raises an alert for a schema mismatch, a conflicting duplicate
run, a missing or non-integer cursor (`cursor_unknown`), a cursor gap or
collision, a run without a finite `started` time, coverage below `min_coverage`,
or a coverage drop larger than `max_coverage_drop`. Duplicates and untimed runs are
counted over all supplied runs, because a run without a trusted time cannot be
placed in a window. A window with no runs, or fewer than
`min_exposure` runs, is `insufficient_evidence`.

The task-mix and performance detectors are not implemented yet. Those channels
always return `insufficient_evidence`, with `statistical_detector_not_implemented`
and `tested: false`, plus `capture_alert_in_window` or `exposure_below_minimum`
where they apply. Their failure rates are descriptive. The receipt's `action` is the
plan's action only when a channel alerts; otherwise it is `observe`.

Every receipt states `regression_label: not_established`, `causal_claim: false`,
`automatic_rollback: false`, `promotion_authority: none` and
`publication_allowed: false`. A drift alert is a reason to look, not evidence that
the harness got worse.
