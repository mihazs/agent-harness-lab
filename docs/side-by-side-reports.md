# Side-by-side experiment review model

`agent_harness_lab.evaluation.side_by_side.build(experiment)` turns one
baseline/candidate experiment record into a read-only, private review model for
[issue #37](https://github.com/mihazs/agent-harness-lab/issues/37). This is the
first stage: the model, pairing and per-pair status. The [static viewer](#static-viewer-ahl-compare)
renders it; nothing else calls this API by default.

## Input

The input is one JSON object with profile `ahl-side-by-side-experiment-v1`:

| Field | Meaning |
| --- | --- |
| `experiment_id` | Exact experiment identity; every trial must repeat it |
| `execution_kind` | `synthetic`, `replay` or `real_model`, declared rather than inferred |
| `baseline_hash`, `candidate_hash`, `grader_hash` | Lowercase SHA-256 identities each arm must match |
| `planned` | Nonempty unique list of `task`/`root`/`family`/`repeat` pair identities |
| `trials` | Arm records: identity, `trial_id`, `arm`, `harness_hash`, `grader_hash`, `runtime_stratum`, `status`, `outcome`, optional `capture`, `usage` and `events` |
| `event_mappings` | Optional explicit `baseline_event_id` to `candidate_event_id` links per pair identity |
| `analysis` | Optional backend analysis, such as the `statistics.analyze` result, with a `status` |
| `resource_decision` | Optional [`resource_decision.decide`](resource-decisions.md) result, shown as supplied |
| `resource_success_cost` | Optional list of 1 to 64 [`resource_decision.success_cost`](resource-decisions.md#descriptive-cost-per-success) views |

A malformed profile, execution kind, hash, plan or analysis object refuses the
whole input. Per-trial problems do not: they stay visible in the report.

## Pairing and verdicts

Records pair by `task`/`root`/`family`/`repeat`, never by list position. Each
pair gets one verdict:

- `improved`, `regressed` or `unchanged` when both arms have exactly one valid,
  graded record with a boolean outcome and matching hashes and runtime stratum.
- `unknown` when an arm is missing (`missing_baseline`, `missing_candidate`) or
  not graded (`ungraded_*`, for example an infrastructure error or cancellation).
  Missing data is never a failure.
- `not_comparable` for unplanned pairs, duplicate arm records or trial IDs,
  invalid records, and harness, grader or runtime mismatches. A lone arm is still
  checked against the experiment's hashes. These pairs stay in the report for
  investigation, with every record, and are excluded from the comparable count
  and usage totals.

Records without a valid identity, and records with an unknown arm, are listed in
`invalid_records` by input index. A rejected arm, hash or runtime stratum is
reported as `null` with its problem code, never copied. A recorded tool error does not change a verdict;
only the graded outcome does.

## Timelines

Each event keeps only `event_id`, `kind`, `name`, `status`, `error_class` and
its input `position`. Every other field, such as message content, tool arguments
or results and reasoning, is dropped and only counted in `omitted_event_fields`.
Events with an unknown kind or malformed metadata are counted in `invalid_events`.

Events align when both arms carry the same `event_id` (`stable_id`) or when an
explicit mapping links them (`explicit_mapping`). Everything else is
`baseline_only` or `candidate_only`, even with the same name at the same
position. A mapping that names a missing event or overrides a shared ID is
rejected and listed in `invalid_mappings`. When two mappings name the same event,
every mapping in that conflict is rejected, whatever the input order; exact
duplicates count as one mapping. Duplicate event IDs in an
arm refuse alignment for that pair. Alignment is display, not causal attribution.

## Unknowns, bounds and authority

A missing or unrecognized `capture` is `unknown`; any pair without `complete`
capture in both arms is a coverage gap. Missing usage stays `null`: each usage
total reports its known sum, known and unknown record counts, and a `total` that
is `null` whenever any record is unknown. Records in `not_comparable` pairs are
left out of these sums and counted in `excluded_records`.

Bounds: 5000 planned pairs, trials and mappings each, refused when exceeded; 200
events per trial, truncated visibly with `events_total` and `events_truncated`;
200 characters per identity or label.

A supplied `analysis` is copied verbatim (`analysis_source: supplied_verbatim`)
and never recomputed. The model sets `pooled_treatment_effect` to `null`,
`causal_attribution` to `none`, `publication_allowed` to `false` and
`automatic_promotion` to `false`. `public=True` is refused, and the public
benchmark exporter rejects the report. Synthetic and replay runs carry
`capability_claim: none`; a real-model run is `unverified`.

## Resource decision and cost per success

A supplied `resource_decision` and `resource_success_cost` list are validated, then copied into the
report (`resource_source: supplied_verbatim`; `not_supplied` otherwise) and never recomputed. The
page shows them after the measured usage: per endpoint its role, kind, unit, scope, horizon, practical
margin, status, mean difference with lower and upper bound, method, independent roots, capped
observations and unknown reasons, so censored and missing measurements stay visible as counts. An
endpoint without a result shows `not computed`, never zero. The cost-per-success table lists, per arm,
assigned trials and successes, the all-assigned mean (failures and timeouts included) beside the
success-conditional mean and the cost per success. An arm with unknown rows shows `unknown`, and an
arm with no success shows `undefined`, never zero.

The whole input is refused, as for a malformed `analysis`, when a block is malformed or contradicts
itself: a favorable decision (`improvement`, `noninferior` or their `conditional_` forms) whose
`quality_status` is not `supported` or that has an unknown, regressed or imprecise required endpoint;
an unknown endpoint that carries a mean or bound; `automatic_promotion` or `publication_allowed` set
to `true`; a cost view marked gate-eligible or passing a gate; a zero-success arm with a cost per
success; an unknown arm with a mean; or a ratio that does not match its status. The check enforces
those invariants only; it does not authenticate the measurements or the quality status, and the
caller-attested labels of the decision are shown as supplied. Not yet shown: total optimization spend
and sample coverage beyond the per-endpoint roots and the pair counts above.

## Static viewer: `ahl compare`

```sh
ahl compare --experiment experiment.json --output NEW_DIRECTORY
```

This renders the model above as one offline page. `--experiment` is a regular
JSON file (a symlink is refused, 16 MiB maximum). `--output` must not exist; it is
created mode 0700 and holds `report.html` and `report.json`, both mode 0600. The
input is never modified. There is no server, browser launch, network access or
model call, and there is no public mode: the report stays private.

The page leads with the supplied backend analysis (status, reason and the full
object, verbatim) and the planned, root, comparable and missing counts, so an
`inconclusive` disposition is the first thing read. Per-pair verdicts, including a
planned pair with no trial shown as `missing`, are text labels in a table; nothing
is coloured by the direction of a point difference, and no decision is computed in
the page. Each pair has a collapsible timeline with its alignment mode. Usage that
is unknown is shown as `unknown`, never zero.

All values are HTML-escaped. The page carries no script, no external asset or link,
and a `default-src 'none'` Content-Security-Policy, and it is usable without
JavaScript. Not yet covered: browser-driven keyboard and network-interception
checks, packaging checks of the viewer inside the wheel, and a public-export mode.
