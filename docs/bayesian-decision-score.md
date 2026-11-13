# A paired Bayesian decision score with a ROPE

This page explains a numeric, non-LLM score that answers whether a candidate harness is
better than, equivalent to or worse than a baseline, or whether the evidence is
insufficient, and why it takes this shape. It is implemented and opt-in: a plan that
declares the frozen `bayes_decision` field gets a `bayesian_decision` block from
`ahl experiment --records`, the history-store analysis that `ahl improve` carries and its
decision receipt, and `ahl compare` displays a supplied block. A plan without the
declaration prints byte-identical output. Calibration of the thresholds and the
descriptive report are not yet implemented (see [Status](#status)).

Use the [how-to](bayesian-decision-how-to.md) to add the score to a plan and read it, and
the [reference](bayesian-decision-reference.md) for the spec field, the output block, the
reasons, the verdict meanings and the six synthetic fixtures. This page keeps the reasoning
and the choices the operator must confirm. Read the [evidence status](#evidence-status)
and the [limits](#limits) before relying on a verdict: the method is an inference by
analogy and the score is recommendation-only.

The existing decision path is unchanged. The weighted-root Hoeffding
analysis in `evaluation/statistics.py` stays the only path that can reach
`recommended` in `promotion/policy.decide`, and `automatic_promotion` stays `false`
everywhere.

## Evidence status

No peer-reviewed work applies a paired Bayesian posterior with a region of practical
equivalence to agent harness scoring. The design is an inference by analogy from three
nearby literatures, none of which was validated for harnesses:

- Bayesian estimation with a region of practical equivalence, from Kruschke,
  "Rejecting or accepting parameter values in Bayesian estimation" (Advances in
  Methods and Practices in Psychological Science, 2018).
- The Bayesian bootstrap over independent units, from Rubin, "The Bayesian
  bootstrap" (Annals of Statistics, 1981).
- Bayesian comparison of classifiers with a practical-equivalence region, from
  Benavoli, Corani, Demsar and Zaffalon, "Time for a change: a tutorial for
  comparing multiple classifiers through Bayesian analysis" (JMLR, 2017).

These citations were written from the literature as known and were not re-fetched
while drafting. They motivate the shape of the score; they do not show that it
controls error for agent tasks. Only the [calibration step](#calibration-planned), run on synthetic
outcome models, can show that for this repository, and even then only under the
declared synthetic model, as `power.py` already says of its own rates.

## Report availability

The research report this design cites was not available, so every claim that depends on
it is unverified. The design brief names it ("Modelos de score para harness", expected
at `research/modelos-de-score-para-harness.md` or `reports/Modelos de score para
harness.md`). It was not in the repository, and a search of the usual document locations
of the machine for file names matching `*modelos*score*` and `*score*harness*`, and of
Markdown files for the phrase "region of practical equivalence", returned nothing.

In particular, this design takes the operator's summary of the report's recommendation
as given:

- Option 2 (paired Bayesian posterior with a ROPE) is the core.
- Option 1 (frequentist) is a cross-check.
- Option 6 is a secondary guard.
- Option 5 is descriptive only.

The identity of Options 1, 5 and 6 beyond those roles is not known from the repository.
This page maps Option 1 to an exact sign-permutation test, Option 5 to a descriptive
desirability and Pareto epsilon-indicator report (the planned optional report), and
Option 6 to a guard that can only downgrade a verdict. Those mappings are guesses and are
listed under open decisions. If the report turns up and disagrees,
the report's text wins over this page's mapping, and this page should be amended.

## Why add a second analysis

The Hoeffding path is distribution-free and conservative. Its own precision page
reports that a half-width of .1 in one family at total alpha .05 needs 877 independent
roots ([experiment precision](experiment-precision.md)). A realistic bank has tens of
roots, so the fixed analysis almost always answers `inconclusive`, and an operator
cannot tell "no evidence" from "probably equivalent" from "probably worse".

A paired posterior answers a different question: given the declared model and the
observed root differences, how probable is it that the candidate is better than the
baseline by more than a practically meaningful amount `epsilon`, equivalent within
`epsilon`, or worse by more than `epsilon`? That is a graded, numeric answer with a
named equivalence region. The price is a model assumption, which is why it is added
beside the Hoeffding path and calibrated against it, not instead of it.

## Estimand

The estimand reuses the one the Hoeffding path already freezes
(`statistics.incomplete_bounds` names it
`frozen_family_weighted_mean_of_selected_root_mean_paired_correctness_differences`):

- A trial pair is one task and one repetition, run once on the baseline and once on
  the candidate. Its difference is candidate outcome minus baseline outcome, one of
  -1, 0 or +1.
- The root difference `d_r` is the mean of the pair differences inside independent
  root `r`. It lies in [-1, 1]. Repetitions and related tasks are averaged here, so
  they are clustered inside the root and never counted as extra independent units.
- The family mean is `theta_f = mean of d_r over the n_f selected roots of family f`.
- The target is `delta = sum over families of w_f * theta_f`, with the frozen family
  weights `w_f` (they sum to 1 and are never renormalized after the fact).

`delta` is the difference in correctness probability over the selected roots, not a
population effect. Roots are the independent unit; the root labels are assumed
independent unless `root_similarity` evidence says otherwise, and that assumption stays
`unverified`, as everywhere else in the repository. When root-similarity groups are
bound, roots in one group are merged into one unit exactly as
`root_similarity.grouped_weights` does for the Hoeffding path, and the Bayesian block
refuses plans where grouping would change the number of units without frozen evidence.

## Model

### Stage 1: Dirichlet over root differences (minimal)

For each family `f` with roots `r = 1..n_f`, the root differences `d_r` are treated as
the observed support of an unknown distribution over root differences, and the
posterior over the family mean is a Bayesian bootstrap with a prior pseudo-root:

    weights (p_0, p_1, ..., p_n) ~ Dirichlet(a0, 1, ..., 1)
    theta_f = p_0 * 0 + sum_r p_r * d_r

Here `p_0` is the weight of one pseudo-root with difference 0 carrying prior
concentration `a0` (default 1), and each observed root carries concentration 1.
Roots are kept as separate Dirichlet categories even when two roots share a value, so
that draws stay coupled in the unknown-outcome bounds below. Then
`delta = sum_f w_f * theta_f`, and families are drawn independently.

Properties that make this a sound minimum:

- The unit is the root. Repetitions only sharpen each `d_r`; they do not add evidence
  weight, which is the clustering requirement.
- The prior pulls toward no difference, so few roots give wide, null-leaning
  posteriors and the score is hard to push up with little data.
- When every pair is a single task and repetition, `d_r` is in {-1, 0, +1} and the
  model is a Dirichlet-multinomial over three classes whose marginals are
  Beta-Binomial. That is the Beta-Binomial minimal case the brief names.
- It needs only `random.gammavariate` from the standard library. Draws are seeded from
  the plan digest, so a result is reproducible and the seed is not a free choice.

Weakness to state plainly: with discrete, bounded root differences and few roots the
Bayesian bootstrap is known to be too confident in its tails. This is why the score is
never read alone: the root floor, the precision floor, the cross-check and the
calibration below all exist to limit that.

### Stage 2: hierarchical per root (optional, after Stage 1 is calibrated)

Stage 2 adds within-root measurement noise and borrows strength across roots of a
family. For each root, pair outcomes are a three-class count (candidate-only pass,
tie, baseline-only pass). Root class probabilities come from
`Dirichlet(kappa_f * pi_f)` around a family-level mean `pi_f`, with a weak prior on
the concentration `kappa_f` evaluated on a fixed grid (the Dirichlet-multinomial
marginal likelihood is closed form, so no sampler beyond seeded Monte Carlo is
needed). The latent root difference is `pi_win - pi_loss`, and `delta` is formed from
the latent differences of the selected roots exactly as in Stage 1.

To keep repetitions clustered, the within-root likelihood counts at most
`m_eff = number of distinct tasks in the root` effective pairs; repetitions of the same
task never raise `m_eff`. Stage 2 is only worth building if calibration shows Stage 1
is too conservative or too loose, and it is accepted only if it passes the same
calibration gates. It does not change the score definition, the thresholds or the
gate order.

## The score and the decision

With `delta` drawn from the Stage 1 posterior (default 20,000 draws, seeded), define:

    S        = P(delta >  +epsilon | data)      # better
    S_worse  = P(delta <  -epsilon | data)      # worse
    S_equiv  = P(-epsilon <= delta <= +epsilon | data)   # practically equivalent

`S` is the headline score. The decision on quality alone is:

| Condition (checked in this order)        | Quality verdict         |
|------------------------------------------|-------------------------|
| `S >= p_cut`                             | `better`                |
| `S_worse >= p_cut`                       | `worse`                 |
| `S_equiv >= p_cut`                       | `equivalent`            |
| otherwise                                | `insufficient_evidence` |

`p_cut` must be greater than .5 so at most one row can hold; the default is .95.
Because the Monte Carlo estimate of a probability has its own error, a verdict is
withheld (`insufficient_evidence`, reason `monte_carlo_margin`) when a score is within
four binomial standard errors of `p_cut`. The report carries `draws`, `seed` and that
standard error so nobody reads three digits of false precision.

Two floors apply before any verdict other than `insufficient_evidence` is allowed:

- Root floor: at least `max(spec.min_roots, 8)` independent roots. The constant 8 is
  the smallest root count where an exact sign-permutation one-sided p-value can fall
  to .004 or below, so a unanimous win does not sit exactly on the cross-check's own
  boundary. It is a design choice to revisit during calibration.
- Precision floor: the central posterior interval at level `p_cut` must have a
  half-width at or below the frozen `precision_half_width`. The Bayesian block
  requires that existing field to be set. It is model-based and looser than the
  Hoeffding precision, so it does not replace the Hoeffding precision preflight; it
  simply stops a wide posterior from producing a verdict.

The score is a graded number, not a threshold on the data. A reader sees `S`, the
other two probabilities, the interval and the floors separately.

## Unknown outcomes

Unknown means a record that is not graded by the executable grader, has a non-boolean
outcome or critical flag, or is missing. This is the same definition `analyze` and
`incomplete_bounds` use. Unknown is never a failure and never a pass.

For each root the pair differences are bounded the way `incomplete_bounds` does it:
the lowest value takes an unknown candidate as 0 and an unknown baseline as 1; the
highest takes the opposite. That gives `d_low_r <= d_r <= d_high_r`. Two posteriors
are then computed with the same Dirichlet weights:

    S_low  = score with every d_r at d_low_r    # worst-case completion
    S_high = score with every d_r at d_high_r   # best-case completion

Because every `d_r` enters `delta` with a non-negative weight and the draws share the
same Dirichlet weights, `delta_low <= delta <= delta_high` draw by draw. So `S_low <=
S <= S_high` for every possible completion of the unknowns, and the same ordering
reversed holds for `S_worse`. The bounds are valid for all completions, not just the
two extremes. They are not a model of why outcomes are missing.

Rules that follow:

- With any unknown arm, no point `S` is reported (`estimate` is `null`, as in
  `incomplete_bounds`). The block reports `S_low`, `S_high`, the counts of unknown
  arms and the root-level ranges.
- By default the verdict with any unknown is `insufficient_evidence` with reason
  `unknown_outcomes`. This keeps the existing rule that an `incomplete` analysis
  recommends nothing. The block adds a `bound_resolution` field: `better_under_all_completions`
  when `S_low >= p_cut`, `worse_under_all_completions` when the best-case
  `S_worse >= p_cut`, `equivalent_under_all_completions` when the equivalence mass is
  at least `p_cut` under both extremes, and `unresolved` otherwise. This tells the
  operator whether collecting the missing outcomes could matter without promoting any
  unknown to a result.
- An unknown critical-regression flag is unknown, not absent. It blocks `better` and
  `equivalent` just as an unknown grade does.
- Allowing a verdict from `bound_resolution` is a less conservative option and is off
  by default (`allow_bound_resolved_verdict: false`); see open decisions.

## The lexicographic gate

Stages run in a fixed order. A later stage can never undo an earlier stage's veto, and
a stronger score never offsets a failed earlier stage.

1. Validity. Provenance hashes equal the frozen plan; the trial set is exactly the
   schedule; root-similarity binding is valid; the independent roots meet the floor;
   the plan is a fixed-final plan. Not met means `blocked`, `incomplete` or
   `insufficient_evidence` (roots), as `analyze` reports today. Sequential plans stay
   `owned_sequential_inspection_required`, and cohort-pinned plans stay on the
   preservation path, which the Bayesian block does not support in the first version
   (as `power.simulate` also refuses them). Oracle validity must be `current`.
2. Critical veto. Any graded executable candidate record with `critical_regression`
   true, or a behavior-observed critical candidate effect, gives `worse` with reason
   `critical_regression`, whatever `S` is. The score is not consulted. This is the
   existing veto carried through, not a new rule.
3. Quality. The score and decision above, plus the sign-permutation cross-check: a
   `better` verdict needs the cross-check not to disagree (below). The cross-check can
   downgrade a verdict to `insufficient_evidence`; it can never raise one.
4. Regression controls. Per family `f`, with `regression_margin` (default the frozen
   `family_margin`): `P(theta_f > -regression_margin) >= p_cut`. A family that fails
   this blocks `better` and `equivalent` and the verdict becomes `insufficient_evidence`
   (reason `family_regression_not_excluded`). If the posterior says a family regressed
   beyond the margin with probability at least `p_cut`, the verdict is `worse`. This is
   the Bayesian counterpart of the Hoeffding path's family noninferiority check and,
   like it, applies to every frozen family.
5. Resource non-inferiority. When the plan pins a resource contract, the existing
   `resource_decision.decide` result is attached and must be `noninferior` or
   `improvement` for a `better` or `equivalent` verdict to stand. If the contract is
   pinned but the stage was not evaluated, the verdict is `insufficient_evidence` with
   reason `resource_gate_not_evaluated`. If no contract is pinned the stage reports
   `not_declared` and does not block. `decide` currently accepts only
   `quality_status == 'supported'` from the Hoeffding path, and the pinned quality
   policy requires `supported`; this design does not change either, so a
   Bayesian-only verdict cannot satisfy the resource stage until a frozen policy
   states otherwise (open decision).

Two additions to an equivalence result: `equivalent` on quality with resource
`improvement` is reported as "equivalent quality, lower cost" and is still descriptive.
A `better` verdict never trades off against a resource regression.

The final output verdict is one of `better`, `equivalent`, `worse`,
`insufficient_evidence`, or `blocked` when stage 1 fails. `blocked` is not a score
outcome.

## Pre-registered parameters

All Bayesian parameters live in one optional frozen `ExperimentSpec` field
`bayes_decision`, so they are part of `ExperimentSpec.digest` and of every trial id
(`plan.schedule` hashes the digest). A plan with no such field serializes exactly as
today: `to_dict` already drops optional fields that are `None`, so existing plan
digests and trial ids do not change. Changing any parameter after a run changes the
plan hash, and the run no longer matches its plan; there is no per-run override.

| Parameter | Default | Why |
|-----------|---------|-----|
| `rope_epsilon` | the frozen `effect_margin`; explicit and in (0, 1) when `effect_margin` is 0 | Reuses the practical-difference threshold the plan already froze, so the two paths agree on what "meaningful" means. Open decision. |
| `prior` | Dirichlet pseudo-root at difference 0, `a0 = 1` per family | One tie-valued pseudo-root pulls toward no difference and is weak against a real bank. A stronger pull is more conservative. Open decision. |
| `p_cut` | .95 (must be > .5) | Matches the 95% convention of the existing Wilson intervals and `alpha` .05. Calibration may only raise it. |
| `regression_margin` | the frozen `family_margin` | Same reason as `rope_epsilon`. |
| `posterior_draws` | 20,000, at most 200,000 | Keeps the Monte Carlo error of a probability near .003 at S near .95. |
| `allow_bound_resolved_verdict` | `false` | Conservative: unknowns never produce a verdict. Open decision. |
| `cross_check_alpha` | `1 - p_cut` | One-sided level that matches the posterior cut. |

The seed is not a parameter: it is derived from the plan digest and a fixed profile
label so a rerun gives the same numbers and nobody can search seeds.

Pre-registration rules: the spec is validated and frozen before any trial record
exists; `analyze` never reads parameters from the records; the held-out calibration
seeds below are fixed in the repository before the calibration run; and no parameter,
fixture expectation or grader is altered in response to a candidate result, per
`AGENTS.md`.

## Frequentist cross-check (Option 1 role)

The cross-check is an exact sign-permutation (randomization) test on the same root
differences and weights. Under the null that root differences are symmetric around
`epsilon` after shifting, flip the sign of each nonzero shifted difference
`d_r - epsilon` and recompute the family-weighted mean. The one-sided p-value is the
fraction of flips with a mean at least as large as the observed one. With at most 20
nonzero roots the enumeration is exact; above that it is a seeded Monte Carlo
permutation test and is labeled `monte_carlo`, never `exact`.

Role and limits:

- It agrees or it does not. `better` needs `p <= cross_check_alpha`; a symmetric
  mirror check applies to `worse`. A disagreement downgrades to `insufficient_evidence`
  with reason `cross_check_disagrees`. It never upgrades a verdict.
- It tests a symmetry assumption, not a causal claim, and with `n` roots its smallest
  possible p-value is `2^-n`, so a very small bank cannot satisfy it. That is one more
  reason for the root floor. The check does not enforce that floor: a six-root bank
  whose roots are all candidate wins reaches the minimum `2^-6`, which the check alone
  accepts at .05, so it is the root floor of the decision that withholds the verdict.
- It does not use the Hoeffding bound and does not replace it.

### The module

`agent_harness_lab/evaluation/sign_permutation.py` has two pure functions and no
dependence on the posterior, a clock, a file or a model.

`sign_permutation(roots, family_weights, epsilon, alpha, *, plan_digest, draws)`
takes the root differences `d_r` of each family (repetitions already averaged inside
the root, root-similarity groups already merged by the caller), the frozen
`family_weights`, `rope_epsilon`, `cross_check_alpha`, the plan digest and a draw
count. Every root of family `f` carries `w_f / n_f`, exactly as the Hoeffding path
does; the weights are not renormalized, and the p-value does not depend on their
scale. It returns the p-value of each side, `better` (shift `d_r - epsilon`) and
`worse` (shift `-d_r - epsilon`), with the number of nonzero roots per side.

- **Exact up to 20 nonzero roots.** Zero shifted differences are dropped, so they are
  neither counted as roots nor allowed to move the p-value, and an input with no
  nonzero root returns p = 1 without error. The count of flips at least as extreme
  as the observed one runs in integer arithmetic by meeting in the middle, so a flip
  that ties the observed value counts and no float rounding can break a tie. Inputs
  are read as the nearest rational with a denominator of at most one million, which
  makes `0.1 + 0.2` and `0.3` tie, as they do in the data they summarize.
- **Seeded Monte Carlo above 20.** `draws` random sign vectors give
  `(1 + hits) / (draws + 1)`, which is never 0, and the result is labeled
  `monte_carlo` with its `draws` and `seed`. The seed is the SHA-256 of the profile,
  a fixed label and the plan digest, so a rerun repeats and nobody can search seeds.
  A result is labeled `monte_carlo` when either side is sampled. The cost is
  `draws * roots` integer additions per side in pure Python.
- **Bounds with unknown outcomes.** Raising one `d_r` moves the observed
  statistic up by at least as much as it moves any flipped statistic, so the `better`
  p-value never rises when a `d_r` rises (and the `worse` p-value never falls).
  Computing the check at the worst-case and the best-case completion of
  the unknown arms therefore brackets the p-value of every completion; an unknown arm
  is never treated as a failure and no point p-value is reported for it. This holds
  for the exact method; a Monte Carlo bracket is itself estimated.

`compare(check, verdict, stage)` compares the quality-stage verdict with the check.
It returns `verdict`, `reason`, `agrees` and `disagrees`. Only a `better` or `worse`
reached at stage `quality` is compared; a validity block, a critical veto, a family
regression control or a resource verdict keeps its own result and sets neither flag,
so the cross-check can never undo a veto or a block. `equivalent` and
`insufficient_evidence` are returned unchanged because the design defines no
cross-check for them. The only possible change is to `insufficient_evidence`, and the
function has no path that raises a verdict. The
[wiring](bayesian-decision-reference.md#wiring) passes the check to the decision function
and the output block.

## Guard role (Option 6)

The report is absent, so Option 6 is only known as "a secondary guard". The design
gives it the narrowest role consistent with that name: a check that can only turn a
verdict into `insufficient_evidence` or `worse` and can never grant anything. The
per-family non-inferiority check and the resource stage above are the guards this
design defines. The operator must confirm whether the report's Option 6 is something
else, for example a worst-case-over-families rule.

## Descriptive-only role (Option 5)

A desirability summary and a Pareto epsilon-indicator report (is the candidate
epsilon-dominated on quality and each resource endpoint) are descriptive. They carry
no verdict, are never an input to the gate, and are labeled `descriptive_only`.
Collapsing quality and cost into one scalar would hide trade-offs the lexicographic
gate keeps explicit, so a single desirability number is never a decision input.

## Relationship to the existing code

Checked against the repository code:

- `statistics.analyze` returns `supported`, `rejected`, `inconclusive`, `incomplete`
  or `blocked`; it already treats critical regression as a veto before unknown grades,
  and `incomplete_bounds` already reports worst and best case without imputation.
- `promotion.policy.decide` returns `recommended` only for a certification-profile
  plan whose analysis is `supported` and whose prerequisites are verified; it sets
  `automatic=False`. This path is untouched.
- `power.simulate` judges each synthetic record set with the unchanged
  `statistics.analyze` and reports a Wilson interval for the supported rate. The
  existing criterion is a Wilson 95% upper bound at or below .05 for the false
  recommendation rate under a null model (300 simulations, seed 29, 40 roots,
  repeats 1 and 5 with correlation 0 and 1, root spread .3).
- `ahl improve` produces its analysis through the controller history store
  (`oracle_history`) and `decision_receipts`; `ahl experiment` calls `analyze` and
  `decide` directly; `ahl compare` copies a supplied analysis verbatim and never
  recomputes it.

So the new block is an additional key on the analysis result, computed by new code
beside `analyze`, not by editing it. `ahl compare` shows the block only when the
supplied analysis already contains it.

## Limits

- Few independent roots give wide, prior-dominated posteriors. That is intended: the
  score says `insufficient_evidence`. A six-root starter bank cannot reach a verdict.
- Repeated looks at one bank are not corrected. Every re-evaluation of the same roots
  is another look, and choosing among several candidates inflates false
  recommendations beyond the single-comparison calibration. The Hoeffding path states
  the same limit. The score is not a sequential test; sequential plans stay on their
  own path.
- Roots are assumed independent. Similar tasks that share a cause behave like one
  root, and the posterior is overconfident when they do not. Root-similarity evidence
  is heuristic.
- The model assumes root differences are exchangeable within a family. A harness whose
  effect differs by root type is better described by the families the plan freezes.
- The score is calibrated only under declared synthetic models. It is not observed
  improvement, certification, oracle validity or native-model identity.
- The score is recommendation-only. It never promotes, never sets
  `automatic_promotion`, never edits the plan or the bank, and `better` does not reach
  `recommended` in `promotion.policy.decide`. Publication stays disallowed.
- No LLM or model call appears anywhere in the score, the cross-check or the gate.

## Status

| Part | State |
|------|-------|
| `bayes_decision` spec field, validation and schema; digests of plans without it unchanged | implemented |
| Stage 1 posterior, floors, unknown bounds and the decision function | implemented |
| exact and Monte Carlo sign-permutation cross-check | implemented |
| the `bayesian_decision` block in `ahl experiment`, the history-store analysis and the decision receipt; display in `ahl compare` | implemented |
| documentation and the six synthetic fixtures | implemented (this page, the [how-to](bayesian-decision-how-to.md) and the [reference](bayesian-decision-reference.md)) |
| calibration with `power.simulate` on declared null, in-ROPE and beneficial models | planned |
| descriptive desirability and Pareto epsilon-indicator report | planned, optional |

Every defect found in this work gets a failing pytest regression test written first, in
`tests/unit/evaluation/` or `tests/integration/evaluation/`.

### Calibration (planned)

Declare and freeze, before running: the models, the seeds, the simulation count, and
the acceptance rule. The rule extends the existing one: under a null model and under a
model with a true effect at half of `epsilon`, the Wilson 95% upper bound of the
`better` rate must be at most .05, the same bound `test_experiment_power` already holds
the Hoeffding path to. Under a beneficial model the Wilson lower bound of the `better`
rate is reported as power, with no pass or fail requirement because the Bayesian path
is meant to be less conservative. `p_cut` may be raised and the root floor may be raised
if the rule fails, and each change is made once on a tuning seed set and confirmed on
a separate held-out set. If the held-out set fails, the work stops and reports; it
does not search for parameters that pass. Dependent-root models (the nominal bound
fails there, as the existing precision page records) are reported, not required to pass.

### Fixtures

Six synthetic banks come from a seeded generator, and the expected verdict of each was
fixed before any number was tuned, so a fixture cannot be adjusted to its result:

- Clear win: two families, many roots, candidate better by well over `epsilon` in
  both. Expected `better`.
- Clear loss: the mirror. Expected `worse`.
- Tie: true difference zero with enough roots to meet the precision floor. Expected
  `equivalent`.
- Mixed with unknowns: a win-shaped bank where a fraction of arms are ungraded.
  Expected `insufficient_evidence` with reason `unknown_outcomes`, no point `S`, and
  `S_low <= S_high`. An unknown outcome is never a failure.
- Critical regression veto: the clear-win bank plus one critical candidate record.
  Expected `worse` with reason `critical_regression`, whatever `S` is.
- Starter bank: six roots, every one a candidate win. Expected `insufficient_evidence`
  (root floor), including when the posterior alone would exceed `p_cut`.

The committed expectations, the commands that reproduce each fixture and a test that runs
them are in the [how-to](bayesian-decision-how-to.md#reproduce-the-six-examples) and the
[reference](bayesian-decision-reference.md#fixtures).

## Open decisions for the operator

- `rope_epsilon`: default is the frozen `effect_margin`. Confirm it, or name the
  smallest correctness difference that matters in practice.
- The prior: a tie-valued pseudo-root of concentration 1 per family. Confirm, or choose
  a stronger pull toward zero.
- Whether `p_cut` stays at .95 before calibration, and whether the root floor of 8 and
  the 20-root exact-enumeration limit are right.
- Whether a verdict may ever come from `bound_resolution` when outcomes are unknown.
- Whether the Bayesian verdict may become an accepted quality status in a new frozen
  resource quality policy. Today only the Hoeffding `supported` is accepted.
- Whether a plan that declares `bayes_decision` should be allowed to execute through the
  controller when only the Hoeffding precision preflight is infeasible, since that
  preflight is what keeps the Bayesian score out of `ahl improve` for small banks.
- Which command should compute `resource_decision.decide` for a recorded analysis, so a
  pinned resource contract can reach `noninferior` rather than `resource_gate_not_evaluated`.
  Today none does.
- Whether an unverified oracle (`ahl experiment`) should block the Bayesian block instead of
  labeling it. The block follows the Hoeffding analysis, which also reports `supported`
  beside `oracle_validity: unverified`.
- What the report's Options 1, 5 and 6 are, since the report was not found.
