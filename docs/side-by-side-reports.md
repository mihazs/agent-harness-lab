# Side-by-side experiment review model

`agent_harness_lab.evaluation.side_by_side.build(experiment)` turns one
baseline/candidate experiment record into a read-only review model, private by default, for
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
| `precision_preflight` | Optional [precision preflight](experiment-precision.md) result (`fixed-root-precision-v1`), shown as supplied |
| `provenance` | Read only by an explicit [public export](#public-export): `source_kind`, `approved_publication`, `source_ref` and `derived_from_private` |

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
  invalid records, harness, grader or runtime mismatches, and an experiment whose
  baseline and candidate hashes are the same (`arms_identical`, one harness
  against itself, see [vacuity labels](vacuity-labels.md)). A lone arm is still
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
`automatic_promotion` to `false`. `publication` is `private` unless the
[public export](#public-export) was requested, and the benchmark exporter
(`benchmark.public_export_allowed`) rejects the report either way, because it is
not a benchmark record. Synthetic and replay runs carry `capability_claim: none`;
a real-model run is `unverified`.

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
model call. The report is private unless `--public` asks for the explicit export
below.

The page leads with the supplied backend analysis (status, reason and the full
object, verbatim) and the planned, root, comparable and missing counts, so an
`inconclusive` disposition is the first thing read. Per-pair verdicts, including a
planned pair with no trial shown as `missing`, are text labels in a table; nothing
is coloured by the direction of a point difference, and no decision is computed in
the page. Each pair has a collapsible timeline with its alignment mode. Usage that
is unknown is shown as `unknown`, never zero.

When the supplied analysis already contains a `bayesian_decision` block (see the
[Bayesian decision score](bayesian-decision-reference.md#wiring)), the page adds a
"Bayesian decision score" section with its verdict, scores, gate stages, cross-check
and resource state as text. It is copied from the analysis, never recomputed, labelled
recommendation-only, and states that no peer-reviewed work applies the method to agent
harness scoring. When the analysis also contains a `descriptive_report` (see the
[descriptive report](bayesian-decision-reference.md#descriptive-report)), a "Descriptive report"
section follows it with the Pareto relation, the epsilon-indicator and the desirability
summary as text, labelled descriptive only and not a verdict; an unknown measurement shows
as `unknown`, never zero. A page whose analysis has no block is unchanged.

When the supplied analysis contains a `preservation` block (the conditional analysis of
[preservation cohorts](preservation-cohorts.md), #122), a "Preservation cohorts" section
follows the leading analysis. It shows, as text copied from the analysis and never
recomputed: the cohort manifest hash, `qualification_authority` and `independence` as
supplied, the seven paired-transition cells of all frozen pairs (baseline pass or fail
against candidate pass or fail, then missing, invalid and unknown) and the same cells per
cohort, each cohort's role, exposure, severity policy, margin, member count, disposition and
interval with its independent roots, the trial coverage counts, the error allocation and the
stated assumptions. Each cohort is its own row, so an aggregate gain cannot hide a protected
loss. A cohort whose interval was not computed shows `unknown` and, for an incomplete
analysis, the supplied `missing_outcome_bounds` as worst and best possible outcomes, not
imputed. A malformed member shows `unknown` or is skipped. The section grants no authority: a
real preservation result is labelled `publication_allowed: false`, so a public export of it is
refused.

When the experiment carries a `precision_preflight` (the fixed-plan precision preflight of
[#9](https://github.com/mihazs/agent-harness-lab/issues/9)), a "Precision-feasibility preflight" section
follows the leading analysis. It shows, as text copied from the supplied result and never recomputed: the
status and whether execution is allowed; the distinct selected-task, declared-root and repetition counts apart
from the planned trials, the trial ceiling and the caller's limit; the half-width target and whether it was met;
every endpoint's roots, margin, half-width, minimum observed effect, best-case lower bound and whether its
frozen criteria are possible; the family weights; the error allocation labels, the qualification, independence,
power and time-feasibility labels as supplied; the solver's descriptive sample-size plan (status, reasons,
required roots, minimum trials); and the stated limitations. A "Why not feasible" row lists, from the supplied
flags only, each required endpoint whose criteria are not possible, an unmet trial ceiling, an unmet target and
the solver's reasons, so an infeasible plan states why it is infeasible rather than being read as a failed
candidate. A value that was not computed shows `not computed`, `not checked` (no root-similarity check) or
`not set`, never zero. The block is refused, like the resource blocks, when it is malformed, claims promotion,
budget or publication authority, or contradicts itself: `feasible` with an unmet trial ceiling or target or a
required endpoint whose criteria are not possible, or execution allowed over an unmet trial ceiling. A preflight
with no frozen target can still allow execution of a plan whose criteria are not possible, and is shown that way.
It is private planning material, so a [public export](#public-export) refuses it.

All values are HTML-escaped. The page carries no script, no external asset or link,
and a `default-src 'none'` Content-Security-Policy, and it is usable without
JavaScript.

Offline checks of the page, without a browser: `tests/unit/evaluation/test_compare_viewer_audit.py`
parses the rendered page and requires that hostile text in every displayed field
(identities, event metadata, the verbatim analysis object, its keys) can only
become text. It allows only the tags and attributes the viewer emits, only
in-page links to an `id` that exists, no `tabindex`, one `h1`, no skipped
heading level, a `summary` first in every `details`, a `scope` on every table
header and a focus style for links and summaries. A mutation that stops escaping
must fail it. The installed-launcher scenario in
`tests/integration/packaging/installed_scenarios.py` runs `ahl compare` from the
release archive and requires the same bytes as the golden page, only
`report.html` and `report.json` in the output and no existing output overwritten.
`tests/integration/packaging/test_installed_layout.py` makes the same check in the source, wheel and
archive layouts of the full suite.

Not yet covered: browser-driven keyboard and network-interception checks (this
audit proves structure, not rendering or focus order in a browser) and the
optimization-receipt (#77), interaction (#123) and review-queue (#73) panels.

## Public export

```sh
ahl compare --experiment experiment.json --output NEW_DIRECTORY --public
```

`--public` is a separate, explicit mode, and `side_by_side.build(experiment, public=True)`
is its model. It applies the same provenance rule as the
[benchmark exporter](analysis.md#public-benchmark-report-input) (`benchmark.public_export_allowed`)
to the experiment's `provenance` declaration: `source_kind` is `synthetic` or
`public`, `approved_publication` is `true`, `derived_from_private` is `false`,
`source_ref` is a short text without control characters, and nothing else is
declared. The declaration is the caller's attestation, not something the tool
can verify: hashed identifiers or redacted text do not turn private data into
public data, so the declaration, not the content, decides.

The whole input is refused, and no output directory is created, when:

- the declaration is missing, malformed, private, not approved or possibly derived from private data;
- any trial carries its own `provenance` that fails the same rule (a mixed input);
- a supplied `analysis` is labelled private: it carries `publication: private`, or a
  `publication_allowed` that is not `true` (an analysis without either label is accepted, as in
  the benchmark exporter);
- a `resource_decision` or `resource_success_cost` is supplied: the resource material is private
  throughout [resource decisions](resource-decisions.md), so it is never exported;
- a `precision_preflight` is supplied: a preflight never grants publication, so it is never exported.

An export keeps every verdict, count and usage total of the private report. It drops
each trial's events, so no event ID, name, status or error class reaches the page or
`report.json`, reads no event mappings, and records only `events_total`, `invalid_events`
and `events_omitted`. The timeline of every pair says `omitted`. `report.json` carries
`publication: explicit_public_export` and a `public_export` block with the declaration,
and the page leads with a public banner and the declaration.

The tool publishes nothing: `publication_allowed` stays `false`, there is no upload, and the
files are written as private (mode 0700 and 0600) like any other report. Moving them anywhere
public is a separate decision by the caller. The export holds only what the declaration
attests, so it is not a privacy review of the supplied `analysis`.
