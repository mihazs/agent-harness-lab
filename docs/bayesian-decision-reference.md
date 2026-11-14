# Bayesian decision score reference

Fields, rules and fixtures of the paired Bayesian decision score. For the reasoning behind
them read the [explanation](bayesian-decision-score.md); for the steps read the
[how-to](bayesian-decision-how-to.md).

No peer-reviewed work applies a paired Bayesian posterior with a region of practical
equivalence to agent harness scoring. The score is an inference by analogy from three
nearby literatures, none validated for harnesses (see the
[evidence status](bayesian-decision-score.md#evidence-status)). The research report this
design cites was not available, so every claim that depends on it is unverified. The score
is recommendation-only and has [limits](bayesian-decision-score.md#limits).

## Spec field

`bayes_decision` (optional, in `ExperimentSpec`, omitted from `to_dict` when `None`):

| Key | Type | Rule |
|-----|------|------|
| `schema_version` | int | 1 |
| `profile` | str | `bayesian-rope-decision-v1` |
| `rope_epsilon` | float | finite, 0 < value < 1; default the frozen `effect_margin` |
| `prior` | object | `kind` `dirichlet_pseudo_root`, `concentration` > 0, `anchor` 0; default one pseudo-root, concentration 1 |
| `p_cut` | float | .5 < value < 1; default .99 |
| `regression_margin` | float | finite, 0 <= value < 1; default the frozen `family_margin` |
| `posterior_draws` | int | 1,000 to 200,000; default 20,000 |
| `allow_bound_resolved_verdict` | bool | default `false` |
| `cross_check_alpha` | float | 0 < value < .5; default `1 - p_cut` |

Validation also requires `precision_half_width` to be set, the plan to be fixed-final
and not cohort-pinned, and unknown keys to be rejected.

Implemented in `evaluation/bayes_spec.py`; the schema of the frozen (resolved) form is
[`schemas/bayes-decision-spec.schema.json`](schemas/bayes-decision-spec.schema.json). The
reasons for each default are under
[pre-registered parameters](bayesian-decision-score.md#pre-registered-parameters).

- **Optional and opt-in.** A plan without `bayes_decision` serializes, hashes and
  schedules exactly as before. An empty object is not an opt-in: `schema_version`
  and `profile` are always required.
- **Defaults resolve at construction.** An omitted `rope_epsilon`, `prior`, `p_cut`,
  `regression_margin`, `posterior_draws`, `allow_bound_resolved_verdict` or
  `cross_check_alpha` is filled with its default when the spec is constructed, and the
  filled values are what `to_dict` and `ExperimentSpec.digest` carry. Later changes to
  `effect_margin` or `family_margin` are therefore separate edits that change the digest
  anyway; they never silently move a frozen Bayesian parameter. `rope_epsilon` has no
  default when `effect_margin` is 0, and `cross_check_alpha` has none when `1 - p_cut`
  rounds to 0; both must then be given.
- **One spelling per value.** Integer fields (`schema_version`, `posterior_draws`,
  `prior.anchor`) reject `20000.0` and `True`. `NaN`, infinities, strings and booleans
  are rejected for every numeric field.
- **Errors are typed.** A rejected declaration raises `BayesDecisionError`, a
  `ValueError` whose `field` names the key, for example `bayes_decision.p_cut`, or
  `sequential`, `precision_half_width` or `cohort_manifest_sha256` for a plan-level
  conflict. `ahl experiment` reports it as an evaluation contract error (exit 2).
- **Frozen.** Any change to any parameter changes the plan digest and every trial id,
  so records of one plan do not fit a plan with an edited declaration. The caller's
  object is copied at construction, and validation re-checks the stored object, so an
  in-place edit is caught either by validation or by the digest check.

## Output block

Key `bayesian_decision` on the analysis result, beside the unchanged fixed analysis. Every
number is recomputed from the frozen plan and the recorded trials of that plan: the
Source column says which. No number comes from a model, a clock or the network, and the
same plan and trials always print the same block.

Sources: `plan` (the frozen `ExperimentSpec`), `trials` (the recorded trial outcomes and
critical flags, bounded where an arm is ungraded), `analysis` (copied from the analysis
the block sits beside), `resource decision` (a `resource_decision.decide` result) and
`constant` (fixed by the profile).

| Field | Meaning | Source |
|-------|---------|--------|
| `profile` | Rule identity, always `bayesian-rope-decision-v1`. | constant |
| `plan_sha256` | Digest of the frozen plan the block was computed for. It also fixes the seed. | plan |
| `parameters`, `parameters.schema_version`, `parameters.profile`, `parameters.rope_epsilon`, `parameters.prior`, `parameters.prior.kind`, `parameters.prior.concentration`, `parameters.prior.anchor`, `parameters.p_cut`, `parameters.regression_margin`, `parameters.posterior_draws`, `parameters.allow_bound_resolved_verdict`, `parameters.cross_check_alpha` | The frozen declaration as resolved at construction (see [Spec field](#spec-field)). Never read from the records. | plan |
| `parameters_sha256` | SHA-256 of the canonical JSON of `parameters`. | plan |
| `draws` | Posterior draws taken; equal to `parameters.posterior_draws`. | plan |
| `seed_source` | Always `plan_sha256_and_profile_label`: the seed is derived from the plan digest, never chosen. | constant |
| `monte_carlo_se` | Binomial standard error of a probability at `p_cut`, `sqrt(p_cut (1 - p_cut) / draws)`. | plan |
| `verdict` | `better`, `equivalent`, `worse`, `insufficient_evidence` or `blocked`; see [Verdict meanings](#verdict-meanings). | trials |
| `reason` | Typed reason for the verdict; see [Reasons](#reasons). | trials |
| `stage` | The gate stage that decided: `validity`, `critical_veto`, `quality`, `regression_controls` or `resource`. | trials |
| `gate`, `gate[]`, `gate[].stage`, `gate[].status` | One entry per stage, in gate order. `status` is `passed`, `failed`, `not_evaluated` (an earlier stage already decided) or `not_declared` (the resource stage of a passed gate when the plan pins no resource contract). | trials |
| `roots`, `roots.selected`, `roots.independent` | Planned roots, and independent units after roots of one root-similarity component merge into one. Repetitions never add roots. | plan |
| `unknown_arms` | Count of arms (one task, repetition and version) that are missing, not graded by the executable grader or without a boolean outcome. An ungraded arm is never a failure and never a pass. | trials |
| `bound_resolution` | `null` with no ungraded arm. Otherwise what the missing outcomes could change: `better_under_all_completions`, `worse_under_all_completions`, `equivalent_under_all_completions` or `unresolved`. It never becomes a verdict by itself. | trials |
| `score` | The posterior of the family-weighted difference `delta`. `null` when a validity block or the critical veto decided before the posterior was drawn. | trials |
| `score.estimate` | `S`, the probability that `delta` exceeds `+rope_epsilon`. `null` with any ungraded arm. | trials |
| `score.S_worse`, `score.S_equiv` | The probability that `delta` is below `-rope_epsilon`, and inside the region of practical equivalence. `null` with any ungraded arm. | trials |
| `score.S_low`, `score.S_high` | Bounds of `S` over every completion of the ungraded arms (worst case and best case). Equal to `S` when no arm is ungraded. | trials |
| `score.S_worse_low`, `score.S_worse_high`, `score.S_equiv_low`, `score.S_equiv_high` | The same bounds for the loss and equivalence probabilities. | trials |
| `score.interval`, `score.interval.level`, `score.interval.low`, `score.interval.high`, `score.interval.half_width` | Central posterior interval of `delta` at level `p_cut`; with an ungraded arm it encloses every completion. `half_width` is what the precision floor compares with `precision_half_width`. | trials |
| `families` | Posterior per frozen family, keyed by family name. `null` when a validity block or the critical veto decided first. | trials |
| `families.<family>`, `families.<family>.roots`, `families.<family>.weight` | One entry per frozen family: its independent units and its frozen weight. | plan |
| `families.<family>.mean` | Posterior mean of the family mean difference. `null` with any ungraded arm. | trials |
| `families.<family>.interval`, `families.<family>.interval.level`, `families.<family>.interval.low`, `families.<family>.interval.high`, `families.<family>.interval.half_width` | Central interval of the family mean at level `p_cut`; it encloses every completion with an ungraded arm. | trials |
| `families.<family>.p_noninferior` | Probability that the family mean exceeds `-regression_margin` (at the worst-case completion with an ungraded arm). | trials |
| `families.<family>.p_regression` | Probability that the family mean is below `-regression_margin` (at the best-case completion with an ungraded arm). | trials |
| `prior_sensitivity`, `prior_sensitivity.half`, `prior_sensitivity.half.concentration`, `prior_sensitivity.half.S`, `prior_sensitivity.double`, `prior_sensitivity.double.concentration`, `prior_sensitivity.double.S` | `S` under half and double the prior concentration, from a separate seeded stream. Report only: it never enters a verdict. `S` is `null` with any ungraded arm; the whole field is `null` when no posterior was drawn. | trials |
| `critical_regression` | `observed` (a graded executable candidate record carries the flag, or the analysis already reports the veto), `unknown` (a flag is missing, ungraded or not a boolean) or `not_observed`. | trials |
| `floors`, `floors.roots`, `floors.roots.required`, `floors.roots.actual`, `floors.roots.met` | The root floor: `required` is the larger of `min_roots` and 8, `actual` the independent units. | plan |
| `floors.precision`, `floors.precision.limit`, `floors.precision.half_width`, `floors.precision.met` | The precision floor: `limit` is the frozen `precision_half_width`; `half_width` is that of `score.interval`, and `half_width` and `met` are `null` when no posterior was drawn. | plan, trials |
| `cross_check`, `cross_check.status` | `evaluated`, or `not_evaluated` when a validity block, the critical veto or the work bound skipped the sign-permutation check. | trials |
| `cross_check.method`, `cross_check.p_better`, `cross_check.p_worse`, `cross_check.basis` | Present when evaluated: `exact` (at most 20 nonzero roots) or `monte_carlo`, the one-sided p-values, and `basis`, `point` or `worst_case_over_completions` when an arm is ungraded. | trials |
| `cross_check.alpha`, `cross_check.agrees` | Present only when a `better` or `worse` quality verdict was compared with the check: the frozen level and whether the check agrees. A disagreement downgrades the verdict and never raises one. | trials |
| `resource_gate`, `resource_gate.status` | `not_declared` (the plan pins no resource contract), `not_evaluated` (a contract is pinned and no resource decision was supplied) or `evaluated`. | plan, resource decision |
| `resource_gate.decision`, `resource_gate.reason`, `resource_gate.contract_sha256` | Present only when evaluated: the attached resource decision, its reason and the contract digest it was made for. | resource decision |
| `recommendation_authority`, `automatic_promotion`, `publication_allowed` | Always `false`. | constant |
| `oracle_validity` | Copied from the analysis the block sits beside (`unverified` in `ahl experiment`). The block never raises it. | analysis |

## Reasons

| Reason | Verdict | Stage | Meaning |
|--------|---------|-------|---------|
| `all_gate_stages_passed` | `better` or `equivalent` | `resource` | Every stage passed; with no pinned resource contract the resource stage is `not_declared`. |
| `posterior_worse_cut_met` | `worse` | `quality` | `S_worse` is at least `p_cut` and, when the cross-check ran, it agrees. |
| `family_regression` | `worse` | `regression_controls` | A family's `p_regression` is at least `p_cut`. |
| `critical_regression` | `worse` | `critical_veto` | A graded executable candidate record carries the critical flag, or the analysis already reports that veto. The score is not consulted. |
| `insufficient_independent_roots` | `insufficient_evidence` | `validity` | Fewer independent roots than the root floor. The score is still reported. |
| `unknown_outcomes` | `insufficient_evidence` | `quality` | An arm is ungraded and the verdict may not come from the bounds (`allow_bound_resolved_verdict` is `false`, or the bounds do not settle it). |
| `insufficient_precision` | `insufficient_evidence` | `quality` | The posterior interval is wider than `precision_half_width`. |
| `monte_carlo_margin` | `insufficient_evidence` | `quality` | A probability lies within four binomial standard errors of `p_cut`. |
| `cross_check_disagrees` | `insufficient_evidence` | `quality` | The sign-permutation p-value of a `better` or `worse` verdict is above `cross_check_alpha`. |
| `posterior_cut_not_met` | `insufficient_evidence` | `quality` | No probability reaches `p_cut`. |
| `unknown_critical_flag` | `insufficient_evidence` | `critical_veto` | A `better` or `equivalent` verdict is withheld because a critical flag is unknown. |
| `family_regression_not_excluded` | `insufficient_evidence` | `regression_controls` | A family's `p_noninferior` does not clear `p_cut`. |
| `resource_gate_not_evaluated` | `insufficient_evidence` | `resource` | The plan pins a resource contract and no resource decision was supplied. |
| `resource_contract_mismatch` | `insufficient_evidence` | `resource` | The supplied resource decision is for another contract. |
| `resource_noninferiority_not_met` | `insufficient_evidence` | `resource` | The supplied resource decision is not `noninferior` or `improvement`. |
| `work_bound_exceeded` | `blocked` | `validity` | `posterior_draws x (independent roots + 3 x families)` exceeds 20,000,000; nothing was sampled. |
| the analysis' own reason | `blocked` | `validity` | Provenance, trial-set, oracle or root-similarity validity failed: for example `zero_trials`, `missing_or_duplicate_trials`, `unexpected_trial_set`, `provenance_mismatch`, `root_similarity_binding_missing_or_invalid` or `root_similarity_component_spans_families`. |

## Verdict meanings

| Verdict | Meaning | Authority |
|---------|---------|-----------|
| `better` | the posterior probability that the candidate beats the baseline by more than `epsilon` is at least `p_cut` and every gate stage passed | none; a review aid beside the unchanged certification path |
| `equivalent` | the posterior probability of a difference inside the ROPE is at least `p_cut` and every gate stage passed | none; not noninferiority certification |
| `worse` | a critical veto, or the posterior probability of a loss beyond `epsilon` or a family regression is at least `p_cut` | none |
| `insufficient_evidence` | no stage vetoed, but evidence does not meet the pre-registered floors or cuts | none |
| `blocked` | validity failed; no score is meaningful | none |

## Decision function

Implemented in `evaluation/bayes_decision.py`. `decide(spec, roots, *, critical, validity,
groups, resource_decision, cross_check)` is pure: no file, clock, network or model call,
and it never calls `statistics.analyze` or `promotion.policy.decide`. The wiring below
calls it once per analysis. It returns the block of the previous section.

- **Inputs.** `roots` holds one `RootDifference(root, family, low, high, unknown_arms)`
  per planned root, exactly the roots of `spec.tasks`. `root_difference(root, family,
  pairs)` builds one from `(candidate, baseline)` outcome pairs (`True`, `False` or
  ungraded), averaging repetitions inside the root so they never add evidence, and
  bounds an ungraded arm the way `statistics.incomplete_bounds` does. `critical` is
  `True` (observed), `False` or `None` (a flag is unknown). `validity` is a typed
  reason when the caller's own stage 1 checks failed. `groups` maps a root to its
  similarity component; roots of one component merge into one unit by their mean,
  and a component that spans families is refused. `cross_check` is the downgrade-only
  input `{method, p_better, p_worse}`; absent means not evaluated and never blocks.
  Malformed input raises `BayesDecisionError`.
- **Sampling.** `random.gammavariate` only. The seed is a SHA-256 of the profile
  label, a stream name and the plan digest (`sampling_seed`), so the same inputs and
  plan give identical output and a different plan gives a different seed. The call is
  bounded: `posterior_draws x (independent roots + 3 x families)` above 20,000,000
  returns `blocked` with reason `work_bound_exceeded` before any sampling.
- **Unknown outcomes.** One pass draws the Dirichlet weights once and applies them to
  the worst-case and best-case root differences, so `S_low <= S <= S_high` holds draw
  by draw for every completion of the ungraded arms. The same holds for the loss
  probability (`S_worse_low`, `S_worse_high`) and the equivalence probability
  (`S_equiv_low`, `S_equiv_high`). The equivalence lower bound is the probability that
  the worst case is above `-epsilon` and the best case below `epsilon` in the same
  draw, which is what holds for every completion; requiring each extreme separately
  would not. With any ungraded arm `score.estimate`, `S_worse` and `S_equiv` are
  `null` and the verdict is `insufficient_evidence` with reason `unknown_outcomes`;
  `bound_resolution` reports `better_under_all_completions`,
  `worse_under_all_completions`, `equivalent_under_all_completions` or `unresolved`.
  With the frozen `allow_bound_resolved_verdict` set to `true`, the same guaranteed
  bounds drive the decision table, the precision floor (an interval that encloses every
  completion) and the family checks (the minimum over completions), so a verdict is
  issued only when it holds for every completion; the reason stays `unknown_outcomes`
  when it does not. `score.estimate` stays `null` either way.
- **Order.** The caller's validity reason, the work bound, then the critical veto
  (before the root floor, as `statistics.analyze` does, so observed harm is reported on
  any bank size), then the root floor (stage `validity`, reason
  `insufficient_independent_roots`), unknown outcomes, the precision floor, the
  Monte Carlo margin and the decision table, the cross-check downgrade of `better` or
  `worse`, family regression (`worse`, reason `family_regression`, when
  `P(theta_f < -regression_margin) >= p_cut`), an unknown critical flag, family
  non-inferiority (`family_regression_not_excluded`) and the resource stage. The
  score is still computed and reported when a floor withholds the verdict, so a reader
  can see that the posterior alone would have passed.
- **Monte Carlo margin.** `monte_carlo_se` is the binomial standard error at `p_cut`,
  `sqrt(p_cut (1 - p_cut) / draws)`. Any of `S`, `S_worse` and `S_equiv` within four
  binomial standard errors of `p_cut` withholds the verdict (`monte_carlo_margin`). A
  family probability counts as meeting the cut only when it is also outside that band.
- **Resource stage.** When the plan pins a contract, a `resource_decision.decide`
  result for the same contract digest with decision `noninferior` or `improvement` lets
  `better` or `equivalent` stand. The attested `conditional_*` decisions do not pass,
  and the stage never touches `worse`. Without a pinned contract the stage reports
  `not_declared`.
- **Authority.** `recommendation_authority`, `automatic_promotion` and
  `publication_allowed` are `false` in every result.

The standard-library sampler follows the Bayesian bootstrap and so is overconfident
with few discrete root differences; the floors and the margin limit that, and
calibration (see the [explanation](bayesian-decision-score.md#calibration-issue-e)) shows
whether they are enough under declared synthetic models: at the first default they were not
for one cell, and the single registered raise of `p_cut` to .99 fixed it.

## Wiring

Implemented in `evaluation/bayes_analysis.py`. `attach(spec, records, analysis, *,
root_similarity_evidence, root_similarity_bank, resource_decision)` returns a copy of
`analysis` with one added key `bayesian_decision`, or `analysis` itself when the plan
declares no `bayes_decision`, so every existing output is byte-identical. It never
calls or edits `statistics.analyze`, `promotion.policy.decide` or
`resource_decision.decide`, makes no model, network, clock or file access, and runs
after the promotion decision, so `decide` never sees the block.

| Command | Where the block appears |
|---------|-------------------------|
| `ahl experiment --spec ... --records ...` | beside `status` and `promotion`; `--preflight`, `--power-model` and the sequential mode are unchanged |
| the history-store analysis (`ahl analyze --history-store`, the decision receipt that `ahl improve` writes and verifies) | in the analysis; the receipt's `analysis_disposition.bayesian_decision` carries verdict, reason, stage and `parameters_sha256` only when the plan declares it, and `analysis_sha256` binds the whole block |
| `ahl compare` | a "Bayesian decision score" section, only when the supplied analysis already contains a block; it is displayed, never recomputed |

How the inputs are read:

- **Roots.** One paired difference per planned root: the mean over its (task,
  repetition) pairs of candidate minus baseline, so repetitions never add evidence.
  Root-similarity components merge into one unit through the existing
  `root_similarity.binding`; a binding that cannot be validated blocks the block
  (`root_similarity_binding_missing_or_invalid`) and a component that spans families
  blocks it (`root_similarity_component_spans_families`) instead of failing the
  analysis.
- **Unknown.** A trial that is missing, not graded by the executable grader or without
  a boolean outcome is an unknown arm, never a failure. A missing trial is an unknown
  arm, not a validity failure, so a partial bank gets bounds and the verdict
  `insufficient_evidence` (`unknown_outcomes`). An unknown critical flag is `unknown`.
- **Validity.** The analysis' own `blocked` reason (provenance, root-similarity, an
  oracle that is quarantined or not sealed, an incomplete behavior observation)
  carries into the block as `blocked`, with every arm reported unknown. Duplicate
  trials (`missing_or_duplicate_trials`), trials outside the schedule
  (`unexpected_trial_set`), a provenance mismatch (`provenance_mismatch`) and an empty
  record set (`zero_trials`, the vacuity label of issue 805) also block it.
- **Critical veto.** A graded executable record with `critical_regression` true, or an
  analysis that already reports the `critical_regression` veto (an observed critical
  behavior effect in the history store), gives `worse` at the `critical_veto` stage
  before any score is read, as `statistics.analyze` does.
- **Cross-check.** The sign-permutation p-values go to `decide` as the downgrade-only
  input, with `draws` equal to the frozen `posterior_draws`. With an ungraded arm the
  p-values hold for every completion of the unknowns (`better` at the worst-case
  completion, `worse` at the best-case one) and `basis` says so. A plan that `decide`
  blocks for its work bound, or a critical veto, skips the check.
- **Resource gate.** No command computes `resource_decision.decide` for a recorded
  analysis yet, so a plan that pins a resource contract reports
  `resource_gate_not_evaluated` instead of `better` or `equivalent`. The `resource_decision`
  argument exists for the day one does; a decision for another contract or one that is
  not `noninferior` or `improvement` blocks `better` and `equivalent`.
- **Oracle.** `ahl experiment` labels the oracle `unverified` and so does the block it
  adds (`oracle_validity`); promotion stays blocked by the existing policy. Only the
  history store can label it `current`.

**Reach in `ahl improve`.** A plan that declares `bayes_decision` must also freeze a
`precision_half_width`, and the controller refuses to execute a plan whose Hoeffding
precision preflight is `infeasible`. The Bayesian block is therefore reachable through
`ahl improve` and `ahl experiment-history` only for banks large enough to pass that
unchanged preflight (hundreds of roots for a half-width of .1, as the
[experiment precision](experiment-precision.md) page reports), which is the regime
where the fixed analysis already answers. Recorded results of any bank size reach the
block through `ahl experiment --records`, and a sealed history of a larger bank carries
it into the analysis and the decision receipt. The preflight is not changed here; the
[open decisions](bayesian-decision-score.md#open-decisions-for-the-operator) name the
question.

A `better` or `equivalent` block next to a Hoeffding `inconclusive` still gives no
recommendation: `promotion.policy.decide` reads only `analysis['status']`, the oracle
label and the prerequisites, and its decision is identical with and without the block.

## Fixtures

Six synthetic banks, built by a seeded generator (`tests/support/bayes_records.py`, which
extends `tests/support/bayes_scenarios.py`) and written by
`scripts/build_bayes_fixtures.py`. Each verdict is committed in
[`examples/bayes-decision/expected-verdicts.json`](../examples/bayes-decision/expected-verdicts.json)
and is fixed by how the bank is built, not by a tuned score: its true paired effect and its
number of roots. Reproduce them with the commands of the
[how-to](bayesian-decision-how-to.md#reproduce-the-six-examples); the tests run the same
commands.

| Fixture | Verdict | Reason | Stage | Fixed analysis | Construction |
|---------|---------|--------|-------|----------------|--------------|
| `clear_win` | `better` | `all_gate_stages_passed` | `resource` | `inconclusive` | Two families of 30 roots. The candidate beats the baseline by a true paired .40, far above `rope_epsilon` .1. |
| `clear_loss` | `worse` | `posterior_worse_cut_met` | `quality` | `inconclusive` | The mirror: two families of 30 roots, a true paired -.40. |
| `tie` | `equivalent` | `all_gate_stages_passed` | `resource` | `inconclusive` | Two families of 60 roots with a true paired difference of 0, enough roots to meet the precision floor. |
| `mixed_unknowns` | `insufficient_evidence` | `unknown_outcomes` | `quality` | `incomplete` | The `clear_win` bank with 15% of its arms ungraded. No point `S`; `S_low` is below `S_high`; no ungraded arm is counted as a failure. |
| `critical_veto` | `worse` | `critical_regression` | `critical_veto` | `rejected` | The `clear_win` bank plus one critical candidate record. The score is not consulted. |
| `starter_bank` | `insufficient_evidence` | `insufficient_independent_roots` | `validity` | `inconclusive` | Six roots, every one a candidate win, in a plan with `min_roots` 2. The posterior alone would exceed `p_cut`; the root floor of 8 withholds the verdict. |

All six plans freeze two equally weighted families, `effect_margin` .1, `family_margin`
.05, `precision_half_width` .15, `posterior_draws` 4000 and 4 repetitions per task, and
declare `bayes_decision` with every other parameter at its default. "Fixed analysis" is
the `status` of the unchanged Hoeffding analysis printed beside the block; none of the six
reaches `recommended`.

## Descriptive report

Key `descriptive_report` on the analysis result, beside `bayesian_decision`, for a plan
that declares `bayes_decision`. Implemented in `evaluation/bayes_descriptive.py` and added
by `bayes_analysis.attach` after the block is finished. It is **descriptive only**: it
carries no verdict, is never an input to the gate, and nothing in the decision path reads
it (`bayes_decision`, `statistics`, `resource_decision`, `promotion.policy` and the decision
receipt do not import it). Its desirability summary and its Pareto epsilon-indicator are
established multi-objective tools applied here by analogy; no peer-reviewed work validates
them for agent harness scoring, and the research report this design cites was not
available. A plan without `bayes_decision` prints no report.

Sources are as for the [output block](#output-block), plus `caller` for the optional
weights of the summary (a reader's choice, never frozen, and no command-line option sets
them: the commands use equal weights).

| Field | Meaning | Source |
|-------|---------|--------|
| `profile` | Always `bayes-descriptive-report-v1`. | constant |
| `descriptive_only` | Always `true`. | constant |
| `decision_input`, `gate_eligible`, `passes_gate`, `recommendation_authority`, `automatic_promotion`, `publication_allowed` | Always `false`. The report is not a decision input, cannot pass or fail a gate and grants nothing. | constant |
| `statement` | The sentence saying so, printed with every report. | constant |
| `epsilon` | The frozen `bayes_decision.rope_epsilon`, the one tolerance of every relation below. | plan |
| `epsilon_source` | Always `bayes_decision.rope_epsilon`: no parameter is introduced here. | constant |
| `resources` | `not_declared` (the plan pins no resource contract, so quality is the one objective), `not_evaluated` (it pins one and no decision was supplied), `contract_mismatch` (the decision is for another contract) or `evaluated`. | plan, resource decision |
| `objectives`, `objectives[]` | One entry per objective: `quality`, then each endpoint of the resource decision as `resource.<name>`; an unusable resource decision gives one `resources` entry. | analysis, resource decision |
| `objectives[].name`, `objectives[].kind`, `objectives[].unit` | The objective's name; for quality `kind` is `quality` and `unit` is `correctness_difference`, for a resource they are the endpoint's `kind` and `unit` (`null` when the decision omits them). | analysis, resource decision |
| `objectives[].status` | `known` (one value), `bounded` (a range, from an incomplete bank) or `unknown`. | analysis, resource decision |
| `objectives[].difference`, `objectives[].difference.low`, `objectives[].difference.high` | Candidate minus baseline in the objective's own unit; `null` when unknown. Quality is the `primary.mean` of the fixed analysis, or the `identified_low` and `identified_high` of its `missing_outcome_bounds`; a resource is the endpoint's `mean_difference`. | analysis, resource decision |
| `objectives[].gain`, `objectives[].gain.low`, `objectives[].gain.high` | The difference on a scale where positive means the candidate is better and the range is [-1, 1]: quality as is, a resource as `-mean_difference / horizon`. `null` when unknown. | analysis, resource decision |
| `objectives[].reason` | Why an objective is unknown: `no_estimate_in_analysis`, `resource_gate_not_evaluated`, `resource_contract_mismatch`, `resource_endpoints_missing`, `malformed_endpoint`, `horizon_undefined`, `measurement_unknown`, `no_mean_difference` or `difference_out_of_range`; `null` otherwise. | analysis, resource decision |
| `epsilon_indicator`, `epsilon_indicator.baseline_vs_candidate`, `epsilon_indicator.baseline_vs_candidate.low`, `epsilon_indicator.baseline_vs_candidate.high` | The additive epsilon indicator of the baseline against the candidate: the smallest epsilon at which the baseline epsilon-dominates the candidate, `max` of the gains. The range covers every value an unknown objective could take. | analysis, resource decision |
| `epsilon_indicator.candidate_vs_baseline`, `epsilon_indicator.candidate_vs_baseline.low`, `epsilon_indicator.candidate_vs_baseline.high` | The same in the other direction, `-min` of the gains: the smallest epsilon at which the candidate epsilon-dominates the baseline. | analysis, resource decision |
| `candidate_epsilon_dominated` | Whether the baseline epsilon-dominates the candidate (`baseline_vs_candidate` at most `epsilon`): `true`, `false`, or `null` when the range straddles `epsilon`. | plan, analysis, resource decision |
| `baseline_epsilon_dominated` | Whether the candidate epsilon-dominates the baseline (`candidate_vs_baseline` at most `epsilon`), with the same three values. | plan, analysis, resource decision |
| `relation` | One word for the two flags, see below. | plan, analysis, resource decision |
| `desirability`, `desirability.value`, `desirability.low`, `desirability.high` | The weighted geometric mean of one ramp per objective: 0 at a gain of `-epsilon` or less, 1 at `+epsilon` or more, linear between. `low` and `high` bound it over the ranges of the gains; `value` is the number when they agree and `null` otherwise. | plan, analysis, resource decision |
| `desirability.weights`, `desirability.weights.<objective>` | The weight of each objective, 1 unless the caller gave one; each must be a positive finite number for a known objective. | caller |
| `desirability.function`, `desirability.combination` | The ramp and the combination, as constants. | constant |

| `relation` | When | Reading |
|------------|------|---------|
| `candidate_dominates` | the candidate epsilon-dominates the baseline and not the reverse | no objective is worse by more than `epsilon` and one is better by more |
| `baseline_dominates` | the baseline epsilon-dominates the candidate and not the reverse | the mirror |
| `epsilon_equivalent` | each epsilon-dominates the other | every objective differs by at most `epsilon` |
| `trade_off` | neither does | one objective is better by more than `epsilon` and another is worse by more |
| `undetermined` | either flag is `null` | an unknown or bounded objective leaves the relation open |

Unknown stays unknown. A resource that is unmeasured, whose horizon is not a positive
finite number or whose difference lies outside its horizon is an `unknown` objective with
a `reason`, and it spans the whole [-1, 1] scale: it is never read as zero cost, a relation
is claimed only when it holds for every value it could take, and an unknown objective can
never make the candidate dominate. Resource usage enters only through a
`resource_decision.decide` result whose `contract_sha256` equals the plan's pinned
`resource_contract_sha256`; no command computes one yet, so the commands print the quality
objective alone (`resources` is `not_declared`) or an unknown `resources` objective when a
contract is pinned. Roots, repetitions and unknown outcomes are handled as in the fixed
analysis the report reads; the report is not a posterior, an interval or a test.

Relations of the six [fixtures](#fixtures), as printed by `ahl experiment --records`
(quality is the one objective; `epsilon` is .1). They are committed in
[`examples/bayes-decision/expected-verdicts.json`](../examples/bayes-decision/expected-verdicts.json)
as `descriptive_relation`:

| Fixture | `relation` | Why |
|---------|------------|-----|
| `clear_win` | `candidate_dominates` | The fixed analysis reports a paired mean of about +.41, above `epsilon`. |
| `clear_loss` | `baseline_dominates` | About -.41. |
| `tie` | `epsilon_equivalent` | About 0, inside the rope. |
| `mixed_unknowns` | `candidate_dominates` | Quality is `bounded` by the ungraded arms; even the worst completion, +.16, is above `epsilon`. The block still withholds its verdict (`unknown_outcomes`). |
| `critical_veto` | `undetermined` | The fixed analysis stops at the veto and reports no estimate, so quality is `unknown`. |
| `starter_bank` | `candidate_dominates` | Six roots, every one a win, a paired mean of 1. The root floor still withholds the verdict. |

`ahl compare` shows a supplied `descriptive_report` as a labelled text section after the
Bayesian block, copied and never recomputed.

Rounding: none; numbers are printed as computed. The report is deterministic and uses no
random draw.
