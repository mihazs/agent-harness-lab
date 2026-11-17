# Harness degradation report

`agent_harness_lab.memory.degradation` turns caller-supplied candidate-versus-baseline metric
facts into an `ahl-harness-degradation-v1` report that says how far a possible degradation is
supported, and no further. It is pure and offline: it reads no store, calls no model, runs no
statistic and authenticates nothing. There is no command yet; the `ahl history compare|trend`
entry points wait for the comparability engine and harness snapshots they read. Examples here are
synthetic.

## Claim ladder

| Claim | Meaning |
| --- | --- |
| `not_observed` | The candidate is unchanged or better on this metric. An improvement is never labelled a regression and no improvement is claimed. |
| `observed_difference` | The candidate is worse, or changed on a neutral metric, but the evidence stops at the point difference. `reasons` names why. |
| `possible_regression` | A worsening point difference with enough independent roots, no missing data and a valid AHL interval that does not cross the frozen worsening margin. |
| `supported_regression_under_test` | The same, and the interval lies wholly beyond the frozen worsening margin: `high < -margin` for a higher-is-better metric, `low > margin` for a lower-is-better one. |
| `causally_attributed` | Reserved for an explicitly controlled design. The module never produces it and `check` rejects it. |

`not_comparable` and `insufficient_evidence` are separate outcomes, not rungs. Each carries named
`reasons`. A claim never exceeds what the comparability verdict allows: `not_comparable` (a
stratum gate such as the grader, task selection or runtime differs) and anything unrecognized or
`insufficient_evidence` stop every metric before any statistic is read, so a change in only the
grader, bank or environment can never be reported as a regression.

## Metric contracts

Each metric is judged on its own; no combined health rating exists.

| Metric | Better | Kind | Extra requirement |
| --- | --- | --- | --- |
| `paired_task_success` | higher | inferential | none |
| `preserved_capability_pass` | higher | inferential | names the lost capabilities; a regression claim without them is `insufficient_evidence` |
| `agent_error_rate` | lower | inferential | counts `agent`, `infrastructure` and `unknown` errors per arm; only agent errors enter the rate, and any unknown error leaves the rate unknown |
| `time_per_assigned_task` | lower | inferential, quality-gated | identical `cache_state` and `timing_boundary` on both arms |
| `measured_tokens` | lower | inferential, quality-gated | identical `cache_state`, `token_category` and `token_scope` on both arms |
| `duplicate_observations` | lower | descriptive | none |
| `surface_presence` | neutral | descriptive | none |
| `finding_coverage` | higher | descriptive | none |

A descriptive metric stops at `observed_difference`. A resource boundary that differs between the
arms makes that metric `not_comparable` (`boundary_changed:<key>`); one that is missing or blank
on either arm is `insufficient_evidence` (`boundary_unknown:<key>`). A quality-gated metric stays at
`observed_difference` with `quality_not_verified` unless `quality_status` is `supported`, matching the
resource decision rule.

## Input

`report(observations, comparability)` takes a mapping of metric name to facts and the comparability
result as `{"verdict": "comparable" | "not_comparable" | "insufficient_evidence", "failures": [...]}`
(each failure a gate name or a mapping with `gate`; `evidence_degradation` is read the same way).

- `baseline` and `candidate`: `identity` (text), `value` (a rate in 0 to 1, or a non-negative number) and
  `denominator` (a positive integer). Resource metrics add a `boundary` mapping. `agent_error_rate` takes `errors` in place of `value`.
- `independent_roots` (after any declared similarity grouping), `min_roots` (frozen, at least 2) and `missing` (units without a usable outcome).
- `statistic`: exactly `method`, `low`, `high`, `alpha` and `margin` for candidate minus baseline, taken from the
  selected AHL analysis. Only `weighted_root_Hoeffding` is accepted; a t-test, a probability of
  degradation, an inverted or non-finite interval, or an interval that excludes the observed difference is unavailable and caps the claim.
- `families`, `surfaces` and, for `preserved_capability_pass`, `lost_capabilities`: lists of names (at most 64). Missing or malformed is `null`, never an empty list.

Anything missing, non-finite, boolean, negative or out of range is unknown. Unknown arm values make the metric
`insufficient_evidence` with a `null` difference; unknown or too few roots, unknown or non-zero `missing` and an
unavailable statistic keep it at `observed_difference`. Many trials never stand in for independent roots.

## Output

`schema`, `ladder`, `comparability`, `metrics`, `not_supplied`, `counters`, `facts_authority`
(`caller_supplied_unverified`), `publication_allowed` (`false`) and `automatic_promotion` (`false`).
Each view shows both identities, values and denominators, the absolute and relative difference and its
direction (`worse`, `better`, `unchanged`, `changed` or `unknown`), `independent_roots`, `min_roots`, `missing`, the
`interval` with its `margin` (only for a possible or supported claim), the shared `boundary`, scope, `caveats` and the comparability
verdict. Metrics the caller did not supply are listed in `not_supplied` as unknown, not healthy.

Counters: `comparisons_not_comparable`, `comparisons_insufficient_evidence`, `candidate_regressions_observed`
(worsening claims of any rung) and `regressions_supported_under_test`.

`check(report)` raises `ValueError` unless every claim carries the numerics its variant needs (a possible or
supported claim cannot hold a null bound, margin, root count or difference), a supported label matches its bound and
margin, the reserved claim is absent and the counters match the views. `render(report)` returns a short text form.

## Trend

`trend(looks)` takes `{"look", "stratum", "report"}` entries and returns a descriptive series per stratum and
metric. It makes no sequential claim: `sequential_claim` is `not_available` and each stratum and metric reports
`error_control_across_looks: none`, because rerunning a fixed-sample bound at each look does not control error
across looks, and no reviewed anytime-valid or alpha-spending procedure exists yet. A look with an unknown stratum, a
repeated id or a report that fails `check` is listed in `unassigned_looks` and never merged.
