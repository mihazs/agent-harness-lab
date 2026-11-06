# Probability-sampled triage audits

Reviewing only classifier alerts cannot show missed incidents, and it cannot
support prevalence or recall estimates for the episodes the classifier stayed
silent on. `agent_harness_lab.diagnostics.audit_sampling` plans a review sample
that includes non-alerted episodes, each drawn with a known positive inclusion
probability, and turns the reviewed labels into design-aware estimates. It is a
library API with no CLI. It runs offline and calls no model or hosted service.

## Frame

A frame freezes one window and one source scope:

```json
{"schema_version":1,"profile":"audit-sampling-frame-v1","scope":"S",
 "window":{"start":"2026-01-01T00:00:00+00:00","end":"2026-01-08T00:00:00+00:00"},
 "units":[{"unit_id":"U","capture":"captured","eligible":true,"alerted":false,"flags":["severe"]}]}
```

Each unit is one root or lineage unit, and the caller declares that units are
independent. `capture` is `captured`, `missing` or `unknown`. Only captured,
eligible units enter the population. The others are counted as `missing_source`,
`unknown_capture` or `ineligible` exclusions in every receipt and report. The
estimand is `captured_eligible_units_in_frame`, not every real interaction.
`flags` may hold `severe`, `uncertain` and `out_of_distribution`. Identical
duplicate unit records collapse and are counted. Conflicting duplicates make the
frame invalid. `freeze(frame)` returns the frame hash and summary.

## Plan and draw

```json
{"schema_version":1,"profile":"audit-sampling-plan-v1","frame_sha256":"<freeze hash>","seed":N,
 "rates":{"random":R,"alerted":R,"severe":R},"review_budget":B,
 "policy_sha256":null,"model_sha256":null,"decision_timestamp":"2026-01-08T01:00:00+00:00"}
```

The letters are placeholders. AHL supplies no default rate, budget or seed.
`random` is required and covers every eligible unit, so each unit has a positive
inclusion probability. The optional `alerted`, `severe`, `uncertain` and
`out_of_distribution` routes oversample units with that alert status or flag. A
route rate must lie in (0, 1]. A zero, negative, non-numeric or out-of-range rate
is refused. The plan binds the frame by hash, so a changed frame is refused.

`draw(frame, plan)` uses Poisson sampling. A unit reached by several routes is
drawn at most once, with inclusion probability `1 - prod(1 - rate)` over its
routes. Its stratum is the set of routes it belongs to. Each unit's uniform
variate comes from SHA-256 of the seed, frame hash and unit hash, so the same
frame and plan replay to the same receipt offline. A plan whose expected review
count exceeds `review_budget` is refused. The realized count varies around the
expected count and is recorded.

The sealed receipt (`audit-sampling-receipt-v1`) records the window, scope, frame
and plan hashes, policy and model identities, decision timestamp, rates, budget,
exclusions, per-stratum inclusion probabilities and expected and realized draws.
It also lists each draw's unit hash, stratum, alert status and inclusion
probability, plus the maximum and minimum weights and the Kish effective sample
size. The receipt is created before any label exists. `review_queue(receipt)`
gives reviewers only the drawn unit hashes in hash order, without alert status,
stratum or score, to reduce anchoring.

## Estimates

`estimate(receipt, batch)` takes labels bound to one receipt:

```json
{"receipt_sha256":"<receipt hash>",
 "labels":[{"unit_sha256":"<hash>","status":"reviewed","label":"tool_misuse"}]}
```

`status` is `reviewed`, `pending` or `nonresponse`. A reviewed label is a class
name or `no_incident`, and the other statuses carry `null`. A drawn unit without
a label is pending. A receipt changed after sealing, a draw whose probability is
invalid or differs from its stratum, labels for another receipt and conflicting
duplicate labels are all refused. Labels for units outside the draw are
convenience reviews with unknown inclusion probability. They are counted as
`outside_probability_sample` and never enter an estimate.

When every draw is reviewed, the status is `estimated`. The report gives
Horvitz-Thompson estimates of incident prevalence, missed incidents (non-alerted
incidents) and missed incidents per class, each as a count and a rate, plus a
linearized ratio estimate of triage recall. Variances use the Poisson design, and
intervals are normal 95% intervals clipped to their range. Recall is `null`, with
the reason `no_sampled_incidents`, when no incident was sampled.

A missing review is not a negative label. Any pending or nonresponding draw makes
the report `inconclusive`, with no point estimates. Its `missing_label_bounds`
fill every missing label first as `no_incident` and then as an incident. These
bounds need no response assumption.

The report always keeps three things apart from the estimates:
`unweighted_composition`, which holds raw per-stratum review counts and is
marked `review_composition_not_population_estimate`; `alert_only`, which counts
incidents among reviewed alerts and is marked `population_support: unsupported`;
and `observed_false_negatives`, the hashes of reviewed non-alerted incidents.
Reviewed weights and the effective sample size are reported too.

Receipts, queues and reports carry unit hashes only, set `publication_allowed:
false` and `automatic_policy_update: false`, and are rejected by the benchmark
public exporter.

## Validation and limits

The unit tests build a fixed synthetic population of 1000 units in which the
classifier never alerts on a rare failure class. Non-alert reviews expose those
false negatives, and the alert-only counts never contain them. Across 200 seeded
replications, the tests check that the bias of prevalence, missed-incident and
recall estimates stays within four Monte Carlo standard errors and that interval
coverage stays within three standard errors of 95%. The tolerances are fixed
constants in the test module. This validates the implementation under that declared
design only, not real-world distributional assumptions.

Normal intervals under-cover when few incidents are sampled. The report shows
the `sampled` count for each estimate. The estimates assume that the declared
units are independent and that reviewer labels are correct. Labels are
unauthenticated, and capture outside the frame is unknown.

This first slice of #75 covers the frame, plan, receipt and fixed-window
estimates. The issue still tracks logged budget changes and adaptive sampling
decisions, cluster designs beyond the declared unit, review-queue export
integration, and versioned summaries consumed by #15 monitoring and the triage
gate. Prediction-powered e-values remain an experimental extension for later.
