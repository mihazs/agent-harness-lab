# Frozen incident-triage policies

`ahl benchmark` can evaluate an explicit binary policy on the input's `heldout`
partition, separately from its descriptive calibration-threshold smoke report:

```sh
ahl benchmark examples.jsonl --method baseline --triage-policy policy.json
```

The policy supplies every constraint; AHL does not invent precision, recall or
coverage targets:

```json
{"schema_version":1,"threshold":0.9,"min_precision":0.8,"min_recall":0.7,"min_coverage":0.5}
```

`threshold` is the minimum confidence `max(p, 1-p)` needed for a decision. Scores
at least 0.5 predict an incident. Multiple observations of one declared root
count once; conflicting decisions within that root abstain. Incident recall
includes positive roots that abstain in its denominator. Unknown labels do not
become negatives, and undefined metrics remain null.

The `triage` object contains the frozen policy hash, threshold, root decisions,
TP/FP/FN/TN, class-specific abstentions and failed constraints. These decisions
use `triage.threshold`; the legacy top-level `predictions` use the separate
descriptive `threshold` and have `threshold_authority: descriptive_smoke_only`.

An observed constraint violation returns `rejected`. Otherwise the result is
`insufficient_evidence`: supplied root IDs and labels do not establish root
independence, independent validation, or oracle authority. No policy from this
interface is accepted for deployment or harness promotion. Freeze the policy
before inspecting validation data; this input format cannot prove that history.

This first slice of issue #74 does not fit a calibrator, tune a policy, issue
uncertainty guarantees, or replace the runtime's existing uncalibrated abstention.

## Multi-class policy, schema v2

`agent_harness_lab.diagnostics.triage_policy.evaluate(policy, observations)` checks
a frozen multi-class policy on held-out declared roots. It is a library API. The
`ahl benchmark` CLI, its binary scorers and the schema v1 output above are
unchanged, and a v2 policy passed to `--triage-policy` is refused as an invalid
v1 schema.

```json
{"schema_version":2,"profile":"diagnostic-triage-policy-v2",
 "classes":{"class_a":{"severity":"severe","threshold":T,"min_precision":P,"min_recall":R},
            "class_b":{"severity":"normal","threshold":T,"min_precision":P,"min_recall":R},
            "class_c":{"severity":"none","threshold":T,"min_precision":P,"min_recall":R}},
 "costs":{"false_alert":C,"wrong_class":C,"missed_alert":C,"severe_miss":C,"review":C},
 "review_capacity_roots":N,"abstention":{"min_margin":M},"min_coverage":V,"max_severe_misses":K,
 "threshold_provenance":"human_preregistered","model_sha256":null,"calibrator_sha256":null,
 "fit_roots_sha256":[],"calibration_roots_sha256":[],"threshold_tuning_roots_sha256":[]}
```

The letters are placeholders. AHL supplies no default threshold, cost, capacity or
acceptance value. Each observation is a dict with `root_case_id`, `split:
"heldout"`, `label` (a declared class or `null`) and `scores` holding one value in
[0, 1] for every declared class. `observations` may be a generator; rows are
aggregated per root in one pass.

Fitting, calibration, threshold selection and validation are separate roles.
This function only validates. It has no threshold search, refuses a missing
class threshold and any `threshold_provenance` other than `human_preregistered`,
and refuses an evaluated root whose SHA-256 appears in the policy's fitting,
calibration or threshold-tuning root list. The policy author declares those
lists. AHL cannot verify that they are complete, or that thresholds were frozen
before validation data were seen.

A row decides its top-scoring class only when that score reaches the class's
frozen threshold and leads the runner-up by at least `min_margin`. Otherwise it
abstains, with the reason `below_class_threshold`, `margin_below_minimum` or
`tied_top_classes`. A root decides only when all its rows agree. Any
disagreement abstains as `conflicting_root_decisions`, and repeated rows count
once. Abstained roots enter a label-free review queue ordered by their highest
severe-class score, then root hash. The first `review_capacity_roots` are routed
to review. The rest are reported as `capacity_overflow`, which fails the
`review_capacity_exceeded` constraint.

The report contains a root-grouped confusion matrix (true class by predicted
class or `abstained`) and per-class TP/FP/FN/TN, abstentions, precision and
end-to-end recall, whose denominator includes abstained roots. It also reports
coverage, abstention reasons, review routing, error counts and the total of the
declared costs. A severe miss is a severe-class root predicted as a non-severe
class, or abstained and not routed to review. Severe misses are counted
separately in `severe_misses`, with their root hashes, even when aggregate
metrics are high. A wrong severe-class alert counts as `wrong_class`. Decisions
and lists carry `root_sha256` only, never raw root IDs. Role-root refusal compares SHA-256 digests of the declared root IDs byte for byte;
IDs are not normalized, so fitting, calibration and threshold-tuning lists must use
exactly the evaluated `root_case_id` strings. Class names may not contain `:` or start
with `abstained`, which is reserved for abstention outcomes.

Any failed constraint returns `rejected`. Otherwise the status is
`insufficient_evidence`, and undefined metrics or classes without validation
roots are listed as reasons. The report has `qualification: descriptive_only`,
`certification: not_issued`, `threshold_selection: refused_in_validation` and
`publication_allowed: false`. Its private `diagnostic-triage-evaluation-v2`
profile and the `diagnostic-triage-policy-v2` policy profile are rejected by
the benchmark public exporter.

This slice establishes the policy and report contract on synthetic roots. It does
not establish classifier quality, calibrated probabilities, uncertainty bounds or
root independence, and it adds no `accepted_for_shadow` path. Human decisions
remain open in #74: preregistering per-use-case thresholds, costs, capacities and
acceptance values; supplying real reviewed labels; and a protected validation
source. The corpus development export releases no validation roots.
