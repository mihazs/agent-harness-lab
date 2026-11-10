# Probability-sampled triage audits

Reviewing only classifier alerts cannot show missed incidents, and it cannot
support prevalence or recall estimates for the episodes the classifier stayed
silent on. `agent_harness_lab.diagnostics.audit_sampling` plans a review sample
that includes non-alerted episodes, each drawn with a known positive inclusion
probability, and turns the reviewed labels into nominal design-aware estimates. It is a
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
is refused. A combined probability that rounds to zero at floating-point precision
is also refused, including for an undrawn stratum. Previously created receipts
with a zero-probability stratum are rejected when reopened, even if that stratum
contains no draws; regenerate the design with representable positive probabilities.
Very small nonzero probabilities
can still have substantial rounding and weight limitations; this is not a numerical
precision guarantee. The plan binds the frame by hash, so a changed frame is refused.

`draw(frame, plan)` approximates Poisson sampling with a deterministic hash. A unit reached by several routes is
drawn at most once, with inclusion probability `1 - prod(1 - rate)` over its
routes. Its stratum is the set of routes it belongs to. Each unit's uniform
variate comes from SHA-256 of the seed, frame hash and unit hash, so the same
frame and plan replay to the same receipt offline. A plan whose expected review
count exceeds `review_budget` is refused. The realized count varies around the
expected count and is recorded. A genuinely certain route includes all its units.
If a combination of non-certain routes rounds to one, the plan is refused rather
than silently treating it as certain. Legacy replay can differ for a certainty
stratum when a previous floating-point hash value rounded to exactly one; other
legacy selections keep their prior rule. The finite 64-bit hash and float mapping
also quantize probabilities below one, so recorded nominal probabilities need not
match the discrete draw probability exactly. The report carries
`finite_precision_hash_sampling_approximate`; design-based properties below are
conditional on the ideal independent-draw model, not exact guarantees for the
implemented hash mapping.

A plan may add `seed_commitment`: `{"sha256":C,"committed_at":T,"nonce":X}`, where
`C = commit_seed(seed, X)` is a SHA-256 of the seed and a secret nonce, published
before the audit window closes. The plan then reveals `seed` and the nonce, and
`draw` refuses it when the seed does not open the commitment or `committed_at` is
not strictly before the window end. The receipt records the commitment and the
revealed seed, and `review_queue` and `estimate` re-run the check, so a receipt
re-sealed with another seed is refused. A seed searched after the fact cannot match
a commitment made earlier, and a commitment made later is refused.

Without a commitment the seed is chosen after the frame is frozen, and someone who
suspects which units are incidents can try seeds offline until the draw leaves them
out; the sealed receipt still validates. Such a receipt keeps the
`seed_selection_unverified` limitation. A receipt that passes the commitment check
drops it and carries `commitment_time_unauthenticated` instead: `committed_at` and
`decision_timestamp` are self-declared, so the check proves only that the seed was
fixed before the stated time.

To make that time trustworthy, add `"anchor":{"kind":"hash_chain_log","entries":[...]}`
to `seed_commitment`. Each entry is `{"time":T,"payload_sha256":H,"prev_sha256":P}`,
where `P` is `anchor_entry_sha256` of the previous entry (`null` for the first). The
first entry's `payload_sha256` must be exactly the commitment hash, entry times must
not decrease and the last entry's time must be strictly before the window end. The
caller passes `trusted_heads` to `draw`, `review_queue` and `estimate`: a mapping of
log head hash to the time that head was observed, copied from a source the planner
does not control (a public checkpoint, a witness, a mirror). The anchor verifies only
when the last entry hash is such a head, observed no earlier than that entry and
strictly before the window end, and only then are `commitment_time_unauthenticated`
and `seed_selection_unverified` both dropped. An anchor over another hash, a broken
chain, an untrusted head or a late time is refused. An anchor with no `trusted_heads`
is recorded but not verified, and a receipt claiming a verified anchor is refused
without them. Verification is offline and standard library only.

What it proves: the commitment hash was in a chain whose head hash a trusted source
saw by the observation time, so the seed was fixed before then. What it does not
prove: that the log is honest or append-only, that the observation time is accurate,
or that the head source is independent. No Git, signature or timestamp-authority
anchors are verified; those need a public key or third-party service and remain
unauthenticated.

The sealed receipt (`audit-sampling-receipt-v1`) records the window, scope, frame
and plan hashes, policy and model identities, decision timestamp, rates, budget,
exclusions, per-stratum inclusion probabilities and expected and realized draws.
It also lists each draw's unit hash, stratum, alert status and inclusion
probability, plus the maximum and minimum weights and the Kish effective sample
size. Reopening checks the declared route probabilities, unique draw identities,
stratum and total counts, and that every unit in a certain stratum is drawn.
Inconsistent legacy receipts are rejected. These internal checks do not authenticate
the frame, seed, reviewer labels or author of a self-sealed receipt. The receipt is created before `estimate` accepts any label. `review_queue(receipt)`
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
nominal inverse-probability estimates of incident prevalence, missed incidents (non-alerted
incidents) and missed incidents per class, each as a count and a rate, plus the
ratio estimate of triage recall. Standard errors use the Poisson design. Recall
is `null`, with the reason `no_sampled_incidents`, when no incident was sampled.

Normal (Wald) intervals under-cover and over-cover when only a few cases are
sampled, so the 95% intervals are exact per stratum instead. Under the Poisson
design, the sampled cases of a stratum with N cases and inclusion probability p
follow Binomial(N, p). A stratum with x sampled cases gets the binomial-index
analogue of the Clopper-Pearson interval: the smallest N with
P(X >= x) >= 0.025 and the largest N with P(X <= x) >= 0.025, searched over
whole counts up to the stratum's unit count. A stratum with no sampled case adds
its zero-event upper bound, so it still widens the interval. A stratum reached
by the `alerted` route holds only alerted units and never enters a missed
count. The per-stratum limits combine into one interval by MOVER (the method of
variance estimates recovery): the lower distances to each stratum's estimate add
in quadrature, and so do the upper distances. Counts and rates are marked
`interval: exact_binomial_mover`. Recall applies MOVER to the logarithm of
missed over alerted incidents, using the two count intervals, and is marked
`interval: exact_binomial_mover_log_ratio`.

When only one stratum can hold the counted case, each exact limit misses the
true count with probability at most 2.5%. The zero-event bound misses with
probability at most 5%. Coverage is therefore at least 92.5% for every true
count, and at least 95% when the zero-event bound reaches the true count. A
combination of several strata, and the recall ratio, are approximations without
that guarantee. Receipts record this as the `multi_stratum_intervals_approximate`
limitation. All of this assumes independent draws at the declared probabilities
and correct reviewer labels.

A Wald interval has zero width when no case was sampled, which would claim
certainty about the quantity being measured. An estimate with `sampled: 0`
instead reports `interval: zero_event_upper_bound`, `standard_error: null` and a
one-sided 95% upper limit: the largest count M with `(1 - p_min)^M >= 0.05`,
where `p_min` is the smallest stratum inclusion probability, capped at the
population. When no missed incident was sampled, the report adds the reason
`no_sampled_missed_incidents`. When every sampled incident was alerted, or none
was, recall reports `interval: zero_event_plug_in_bound`, which puts that upper
limit for the unsampled side into the ratio with the other side's point estimate.
`missed_by_class` lists only classes seen among sampled incidents. An unseen
class is covered by the overall `missed_incidents` bound.

A missing review is not a negative label. Any pending or nonresponding draw makes
the report `inconclusive`, with no point estimates. Its `missing_label_bounds`
give, for prevalence and the missed-incident rate, the lower 95% limit with every
missing label filled as `no_incident` and the upper 95% limit with every missing
label filled as an incident, clipped to [0, 1]. They make no assumption about
why labels are missing and use the same exact and zero-event intervals as the
estimates. They are `null` when nothing was drawn.

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
coverage stays within three standard errors of 95% on both sides, so intervals
that are too wide fail as well as intervals that are too narrow. A closed-form
case checks the Poisson-design variance exactly. Separate simulations check that
the zero-event bounds and missing-label bounds cover the truth at no less than
the nominal rate.

Small-count tests enumerate every sample outcome, so their coverage is exact,
not simulated. With 1 to 5 missed incidents sampled on average at random rates
of 0.05 and 0.2, missed-incident coverage stays within the same band on both
sides of 95%. Triage recall stays in the band with 10 alerted incidents drawn at
0.525 and 1 to 5 sampled misses drawn at 0.05. A design that pairs a stratum
drawn with certainty with a 0.05 stratum stays in the band too, including the
outcomes where the 0.05 stratum has no sampled miss. The normal intervals
these replace failed 11 of those 16 cases, with coverage from 0.64 to 1.0. The
tolerances are fixed constants in the test module. This validates the
implementation under that declared design only, not real-world distributional
assumptions.

The report shows the `sampled` count for each estimate. The estimates assume
that the declared units are independent and that reviewer labels are correct.
Labels are unauthenticated, and capture outside the frame is unknown.

This first slice of #75 covers the frame, plan, receipt and fixed-window
estimates. The issue still tracks logged budget changes and adaptive sampling
decisions, cluster designs beyond the declared unit, review-queue export
integration, and versioned summaries consumed by #15 monitoring and the triage
gate. Prediction-powered e-values remain an experimental extension for later.
