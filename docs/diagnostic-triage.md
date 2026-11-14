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

The default binary report uses its historical labeled-root coverage denominator.
To evaluate coverage across every distinct declared held-out root, including
unlabeled abstentions and decisions, pass `--triage-report-version 2` with
`--triage-policy`. The separate `diagnostic-binary-triage-evaluation-v2` profile
reports `declared_roots` and `decided_roots`; its coverage is their ratio, or
null when there are no roots. This changes the coverage constraint's denominator,
not the labels used for precision and recall. Repeated rows of one root count
once. The report is private and does not verify labels or root independence.

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
declared costs. If a cost total exceeds the finite floating-point range or the
active integer-to-JSON digit limit, the total is null and
`cost_total_unrepresentable` is listed as a reason. All-integer cost arithmetic
remains exact when the total can be serialized. A severe miss is a severe-class root predicted as a non-severe
class, or abstained and not routed to review. Severe misses are counted
separately in `severe_misses`, with their root hashes, even when aggregate
metrics are high. A wrong severe-class alert counts as `wrong_class`. Decisions
and lists carry `root_sha256` only, never raw root IDs. Role-root refusal compares SHA-256 digests of the declared root IDs byte for byte;
IDs are not normalized, so fitting, calibration and threshold-tuning lists must use
exactly the evaluated `root_case_id` strings. Class names may not contain `:` or start
with `abstained`, which is reserved for abstention outcomes.

The default v2 report measures coverage only among labeled roots. For coverage
over all distinct declared roots, including unlabeled decisions and abstentions,
call `evaluate(policy, observations, report_version=3)`. Its separate
`diagnostic-triage-evaluation-v3` profile uses that denominator for the coverage
constraint. Historical v2 and v3 coverage values are not directly comparable.
Neither profile establishes label authority or root independence.

For a strict declared-role check, call `evaluate(policy, observations,
report_version=4)`. The separate `diagnostic-triage-evaluation-v4` profile uses
the v3 all-root coverage denominator and refuses any root hash listed in more
than one fitting, calibration or threshold-tuning role. It also retains the
existing refusal of held-out roots in those lists. The check compares only the
caller-supplied lists: empty, incomplete or falsified lists can pass, and
disjoint hashes do not prove independence, calibration or prior freezing. This
profile also requires the policy's `model_sha256` and refuses any observation whose
own `model_sha256` differs or is missing, so out-of-scope model revisions are
rejected rather than evaluated; the revision match is a caller-supplied
identity, not verified provenance. The strict profile does not express cross-fitting; historical v2 and v3 behavior
is unchanged. The v4 report is private and grants no certification or promotion
authority.

For descriptive score diagnostics, call `evaluate(policy, observations,
report_version=5)`. The separate `diagnostic-triage-evaluation-v5` profile applies
every v4 check and decision unchanged and adds `score_diagnostics`, computed on
labeled roots from each root's mean row score (repeated rows count once;
abstaining roots are included because these describe scores, not decisions):
a multiclass Brier score, a top-label reliability table over the fixed bins
[0, .2), [.2, .4), [.4, .6), [.6, .8) and [.8, 1] with root counts, mean
confidence and accuracy, and a risk/coverage table giving, for the same fixed
minimum confidences, the share of labeled roots kept and their misclassification
rate. Bins with fewer than 10 roots are counted in `sparse_bins` and add the
`reliability_bins_sparse` reason; 10 is a reporting convention, not an acceptance
value. Empty bins and undefined rates are null. Brier and the tables do not
feed any constraint, so a low Brier score cannot rescue a failed recall or
coverage check, and scores are not assumed to be calibrated probabilities.

For a data-revision and calibration-size scope, call `evaluate(policy, observations,
report_version=6)`. The separate `diagnostic-triage-evaluation-v6` profile applies
every v5 check, decision and diagnostic unchanged and requires two additional
policy fields that no earlier profile accepts: `corpus_sha256` (the SHA-256 of the
data revision the policy is scoped to) and `min_calibration_roots` (a nonnegative
integer). AHL supplies no default for either. It refuses any observation whose own
`corpus_sha256` differs or is missing, so rows from an out-of-scope data revision
are rejected rather than evaluated, and echoes `corpus_sha256` beside the policy and
model hashes. When the policy declares a `calibrator_sha256` and lists fewer
`calibration_roots_sha256` than `min_calibration_roots`, the report is
`rejected` with `calibration_roots_below_minimum`; a policy that declares no
calibrator is not affected. Both checks compare caller-supplied identities and
declared hash lists: they cannot show that the corpus hash names the data actually
scored, that the calibrator was fitted on those roots, or that the minimum was
chosen before validation data were seen. Earlier profiles and `ahl benchmark` are
unchanged, and the v6 report is private and grants no certification or promotion
authority.

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
