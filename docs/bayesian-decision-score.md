# A paired Bayesian decision score with a ROPE

This page explains a numeric, non-LLM score that answers whether a candidate harness is
better than, equivalent to or worse than a baseline, or whether the evidence is
insufficient, and why it takes this shape. It is implemented and opt-in: a plan that
declares the frozen `bayes_decision` field gets a `bayesian_decision` block from
`ahl experiment --records`, the history-store analysis that `ahl improve` carries and its
decision receipt, and `ahl compare` displays a supplied block. A plan without the
declaration prints byte-identical output. The default `p_cut` was calibrated with the
seeded power simulator under a frozen protocol (see [Calibration](#calibration-issue-e)).
The descriptive desirability and Pareto epsilon-indicator report is implemented and kept apart
from every decision (see [Descriptive-only role](#descriptive-only-role-option-5)).

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
controls error for agent tasks. Only the [calibration step](#calibration-issue-e), run on synthetic
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
desirability and Pareto epsilon-indicator report (the descriptive report), and
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

`p_cut` must be greater than .5 so at most one row can hold; the default is .99, raised from .95 by the [calibration](#calibration-issue-e).
Because the Monte Carlo estimate of a probability has its own error, a verdict is
withheld (`insufficient_evidence`, reason `monte_carlo_margin`) when a score is within
four binomial standard errors of `p_cut`. The report carries `draws`, `seed` and that
standard error so nobody reads three digits of false precision.

Two floors apply before any verdict other than `insufficient_evidence` is allowed:

- Root floor: at least `max(spec.min_roots, 8)` independent roots. The constant 8 is
  the smallest root count where an exact sign-permutation one-sided p-value can fall
  to .004 or below, so a unanimous win does not sit exactly on the cross-check's own
  boundary. The calibration kept it at 8: no cell at the floor failed the acceptance rule
  (see [Calibration](#calibration-issue-e)).
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
| `p_cut` | .99 (must be > .5) | Started at .95, the convention of the existing Wilson intervals and `alpha` .05. The calibration failed one required cell on the tuning seeds at .95 and raised it once to .99, which held on the held-out seeds (see [Calibration](#calibration-issue-e)). |
| `regression_margin` | the frozen `family_margin` | Same reason as `rope_epsilon`. |
| `posterior_draws` | 20,000, at most 200,000 | Keeps the Monte Carlo error of a probability near .0007 at S near .99. At `p_cut` .99 fewer than about 1,600 draws can never clear the four-standard-error margin, so such a plan returns only `insufficient_evidence` with reason `monte_carlo_margin`. |
| `allow_bound_resolved_verdict` | `false` | Conservative: unknowns never produce a verdict. Open decision. |
| `cross_check_alpha` | `1 - p_cut` (.01 at the default) | One-sided level that matches the posterior cut. |

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

It is implemented in `evaluation/bayes_descriptive.py` and printed as `descriptive_report`
beside the block (see the [reference](bayesian-decision-reference.md#descriptive-report)
and the [how-to](bayesian-decision-how-to.md#read-the-descriptive-report)). How it is
kept apart from the decision:

- The block is finished before the report is computed, and the report reads the fixed
  analysis and a resource decision, never the block. Nothing in the decision path imports
  it, and tests change the weights, the indicator and the whole report and require the
  block, the fixed analysis and the promotion to stay identical.
- It adds no parameter. Its one tolerance is the frozen `rope_epsilon`, and the optional
  weights of the summary belong to the reader, are not frozen and have no command-line
  option; a test shows no weight reaches the block.
- It has no pass or fail key: `passes_gate` and `gate_eligible` are always `false`, and
  there is no verdict key.

What it computes, with the conservative choices it took where the design was open:

- **Objectives on one scale.** Each objective becomes a gain in [-1, 1], positive when the
  candidate is better: the paired correctness difference as is, and each resource
  difference as `-mean_difference / horizon` (a lower cost is a gain). Dividing by the
  frozen horizon needs no ratio of costs, so a zero or unknown baseline cost cannot
  produce an undefined ratio; a horizon that is not a positive finite number makes the
  objective unknown. Using one epsilon for quality and for resources is a convention, not
  a calibrated trade-off rate, and is listed under the open decisions.
- **Epsilon-indicator.** The additive indicator of Zitzler and co-authors (2003) for two
  points: with the baseline at the origin and the candidate at its gains, the smallest
  epsilon at which the baseline epsilon-dominates the candidate is the largest gain, and
  the reverse is the negated smallest gain. The report prints both and, at `rope_epsilon`,
  whether each dominance holds. Neither direction holding is a `trade_off`, not a failure.
  The function takes fronts of any size and is checked against hand-computed two- and
  three-point fronts.
- **Desirability.** The weighted geometric mean of a ramp per objective, in the manner of
  Derringer and Suich (1980): 0 at a loss of `rope_epsilon`, 1 at a gain of it. One
  unacceptable objective makes the whole summary 0, which is the point of the geometric
  mean and also why it is only a picture.
- **Unknown stays unknown.** An unmeasured resource, or one whose decision is for another
  contract, is an unknown objective that spans the whole scale. A relation or a summary
  `value` is stated only when it holds for every value the unknown could take; otherwise
  the relation is `undetermined` and `value` is `null`. An unknown cost is never zero cost,
  and it cannot make a candidate dominate. Quality from an incomplete bank is the range
  the fixed analysis already reports, not an imputed point.
- **Quality is one objective.** The report does not show per-family objectives, so a
  family regression that the gate catches can sit beside a `candidate_dominates` report;
  the block decides.

Both tools are established in the multi-objective literature, but, as for the rest of this
page, no peer-reviewed work applies them to agent harness scoring: using them here is an
inference by analogy, cited from the literature as known and not re-fetched.

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
- The acceptance rule of the calibration bounds the `better` rate only. Under the null
  at the 8-root floor the calibration also observed false `worse` verdicts (the family
  regression control), with Wilson upper bounds above .05 in two cells; see
  [Calibration](#calibration-issue-e).
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
| calibration with the power simulator's generator on declared null, in-ROPE, beneficial and dependent-root models; held-out seeds fixed before the run | implemented (see [Calibration](#calibration-issue-e)) |
| descriptive desirability and Pareto epsilon-indicator report, descriptive only | implemented (see [Descriptive-only role](#descriptive-only-role-option-5)) |

Every defect found in this work gets a failing pytest regression test written first, in
`tests/unit/evaluation/` or `tests/integration/evaluation/`.

### Calibration (issue e)

Implemented in `evaluation/bayes_calibration.py`. The numbers below describe the declared
synthetic models and nothing else: they are not observed error control for real tasks,
real task dependence or real roots, as `power.py` says of its own rates.

**What was declared before any run.** The protocol (`bayes_calibration.PROTOCOL`, SHA-256
`00b16f5deccbd21d638c228466a40b08f900643eef69be8a6e4b2048fae700c0`) was committed before
the first simulation, and the recorded results carry its digest so a later edit is caught
(see the guards below).

- Records are drawn by the power simulator's own generator with the same seed handling as
  `power.simulate`, then judged twice: by the unchanged `statistics.analyze` (its
  `supported` count is reported beside, never used) and by the `bayesian_decision` block
  that `bayes_analysis.attach` adds. Nothing is run, observed or graded, and no model is
  called.
- The plan: one family, `effect_margin` .1 (so `rope_epsilon` .1), `family_margin` .05,
  `alpha` .05, `min_roots` 3 (root floor 8), the default 20,000 posterior draws and a
  `precision_half_width` of 1. The precision floor can only withhold a verdict, so
  setting it so wide that it never does gives the highest `better` rate a plan can have;
  a tighter floor can only lower it.
- The outcome model: baseline success .5 and root spread .3, as in
  `test_experiment_power`, in the same two layouts: repeats 1 with repeat correlation 0
  and repeats 5 with repeat correlation 1.
- 300 simulations per cell. The tuning seeds are 3100 plus the cell index and the held-out
  seeds 4100 plus the cell index; the fast guard uses 5150 and up.

| Model | Role | True effect | Roots | Layouts |
|-------|------|-------------|-------|---------|
| `null` | required | 0 | 8 (the floor) and 40 | both |
| `half_epsilon` | required | .05, half of `rope_epsilon` | 8 and 40 | both |
| `beneficial` | power, reported | .3, three times `rope_epsilon` | 40 | both |
| `clusters_of_4` | dependent, reported | 0, each run of four consecutive roots shares one outcome | 40 | repeats 1 |
| `one_cluster` | dependent, reported | 0, every root shares one outcome | 40 | repeats 1 |

**Acceptance rule.** In every required cell the Wilson 95% upper bound of the `better`
rate is at most .05, the bound `test_experiment_power` holds the Hoeffding path to. Power
under the beneficial model is the Wilson lower bound of the `better` rate, with no pass or
fail. Dependent-root models are reported, not required (the nominal bound fails there, as
the [precision page](experiment-precision.md#simulated-power-under-a-declared-outcome-model)
records). The tuning seeds run the required cells only; the held-out seeds run all cells.
The only adjustment the protocol allows is one raise of `p_cut` to .99 (and of the root
floor to 12), taken only if a required tuning cell fails and then confirmed once on the
held-out seeds; if the held-out seeds fail, the work stops and reports.

**What happened.**

1. At the default `p_cut` .95 the tuning seeds failed one required cell: 9 of 300 in
   `half_epsilon/roots40/repeats5-corr1`, Wilson upper bound .056. The other seven passed.
2. The single raise was applied: the default `p_cut` is now .99, and `cross_check_alpha`
   follows its default of `1 - p_cut`, .01. The root floor was not raised: every cell at
   the floor passed (upper bound at most .029), and the floor cannot affect a 40-root
   bank. This applies the registered raise of `p_cut` only, not the registered root floor
   of 12, because no evidence called for it.
3. The held-out seeds confirmed the raise: every required cell has an upper bound of
   at most .024. The tuning seeds at .99 also pass, with a largest upper bound of .029.

Tuning seeds at the first default, `p_cut` .95 (failed):

| Cell | `better` | Rate | Wilson 95% interval |
|------|---------:|-----:|---------------------|
| `null/roots8/repeats1-corr0` | 2 of 300 | 0.007 | 0.002 to 0.024 |
| `null/roots8/repeats5-corr1` | 2 of 300 | 0.007 | 0.002 to 0.024 |
| `null/roots40/repeats1-corr0` | 0 of 300 | 0.000 | 0.000 to 0.013 |
| `null/roots40/repeats5-corr1` | 1 of 300 | 0.003 | 0.001 to 0.019 |
| `half_epsilon/roots8/repeats1-corr0` | 3 of 300 | 0.010 | 0.003 to 0.029 |
| `half_epsilon/roots8/repeats5-corr1` | 3 of 300 | 0.010 | 0.003 to 0.029 |
| `half_epsilon/roots40/repeats1-corr0` | 6 of 300 | 0.020 | 0.009 to 0.043 |
| `half_epsilon/roots40/repeats5-corr1` | 9 of 300 | 0.030 | 0.016 to 0.056 |

Held-out seeds at `p_cut` .99, required cells (passed):

| Cell | `better` | Rate | Wilson 95% interval | Hoeffding `supported` |
|------|---------:|-----:|---------------------|---------------------:|
| `null/roots8/repeats1-corr0` | 0 of 300 | 0.000 | 0.000 to 0.013 | 0 of 300 |
| `null/roots8/repeats5-corr1` | 0 of 300 | 0.000 | 0.000 to 0.013 | 0 of 300 |
| `null/roots40/repeats1-corr0` | 0 of 300 | 0.000 | 0.000 to 0.013 | 0 of 300 |
| `null/roots40/repeats5-corr1` | 1 of 300 | 0.003 | 0.001 to 0.019 | 0 of 300 |
| `half_epsilon/roots8/repeats1-corr0` | 1 of 300 | 0.003 | 0.001 to 0.019 | 0 of 300 |
| `half_epsilon/roots8/repeats5-corr1` | 0 of 300 | 0.000 | 0.000 to 0.013 | 0 of 300 |
| `half_epsilon/roots40/repeats1-corr0` | 2 of 300 | 0.007 | 0.002 to 0.024 | 0 of 300 |
| `half_epsilon/roots40/repeats5-corr1` | 0 of 300 | 0.000 | 0.000 to 0.013 | 0 of 300 |

Tuning seeds at `p_cut` .99, required cells (passed): the largest count is 3 of 300 in
`half_epsilon/roots40/repeats5-corr1`; the full table is in the recorded results.

Power on the held-out seeds, reported with no pass or fail:

| Cell | `better` | Rate | Wilson 95% interval | Hoeffding `supported` |
|------|---------:|-----:|---------------------|---------------------:|
| `beneficial/roots40/repeats1-corr0` | 97 of 300 | 0.323 | 0.273 to 0.378 | 1 of 300 |
| `beneficial/roots40/repeats5-corr1` | 113 of 300 | 0.377 | 0.324 to 0.433 | 0 of 300 |

At three times `rope_epsilon` with 40 roots the Bayesian rule at `p_cut` .99 recommends
`better` in roughly a third of simulations, and the Hoeffding path in none or one of 300.
The Bayesian path is less conservative than the Hoeffding path on the same records and is
still far from powerful at this bank size; both are conditional on the model.

Dependent roots on the held-out seeds, reported with no pass or fail (the true effect is
zero, so every `better` is a false recommendation):

| Cell | `better` | Rate | Wilson 95% interval | Hoeffding `supported` |
|------|---------:|-----:|---------------------|---------------------:|
| `clusters_of_4/roots40/repeats1-corr0` | 20 of 300 | 0.067 | 0.044 to 0.101 | 2 of 300 |
| `one_cluster/roots40/repeats1-corr0` | 70 of 300 | 0.233 | 0.189 to 0.284 | 70 of 300 |

Roots that share an outcome break the independence the posterior assumes. With every root
in one cluster the false `better` rate is 70 of 300, the same count as the Hoeffding
path's `supported`, so the Bayesian block is no safer than the existing analysis under
full dependence; with clusters of four it is 20 of 300. The block does not detect
dependence.

**Not covered by the rule.** The rule bounds `better`. At the 8-root floor the held-out
seeds also show false `worse` verdicts under the null, from the family regression control:
10 of 300 (Wilson upper bound .060) at repeats 1 and 7 of 300 (.047) at repeats 5, and 13
of 300 (.073) and 7 of 300 (.047) under the half-epsilon model, where the true effect is
positive. Those rates were not part of the declared rule and nothing was changed in
response. They are listed under the open decisions.

**Reproduce.** `task calibrate:bayes-decision SPLIT=tuning PYTHON=.venv/bin/python` or
`SPLIT=held_out` prints one split's JSON report, about ten and twenty minutes on one core.
On a shared machine run it under the shared test lock and a memory cap. The recorded
reports (the failed tuning run at .95, the tuning run at .99 and the held-out run) are
`tests/fixtures/bayes_calibration/results.json`. The task runs the committed defaults;
the failed run at .95 is reproduced by `run_cell('tuning', index, bayes=dict(p_cut=.95))`,
as the replay test does for its first ten simulations.

**Guards in the fast suite** (`tests/unit/evaluation/test_bayes_calibration.py`):

- the protocol digest is pinned twice, in the test and in the recorded results, so a change
  to the models, seeds, simulation count or rule after results are recorded fails;
- the recorded counts, Wilson intervals and acceptance flags are recomputed from the
  recorded per-simulation sequences, the held-out required cells must pass, and the first
  ten simulations of each held-out required cell are replayed with the current code and
  must equal the recorded ones, so a change that alters a decision requires a new
  calibration;
- a live guard reruns the null cells and the binding half-epsilon cell with 4,000 posterior
  draws (1,000 draws cannot reach any verdict at `p_cut` .99, which a test records) on
  seeds that were never part of the calibration, and fails if the Wilson upper bound of
  the `better` rate exceeds .05;
- deliberate-mutation tests: lowering `p_cut` and the cross-check level, and weakening the
  posterior cut to a coin flip with a cross-check that always agrees, must trip the same
  acceptance rule.

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
- The calibrated `p_cut` of .99 and the root floor of 8 stand under the declared models.
  Confirm them, and decide whether the acceptance rule should also bound the false `worse`
  rate: at the 8-root floor it exceeded .05 in its Wilson upper bound in two cells, and
  raising the floor (the other registered lever) was not triggered because the rule as
  declared covers `better` only. The 20-root exact-enumeration limit was not calibrated.
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
- Whether the descriptive report should use one epsilon (`rope_epsilon`) for quality and for
  resource gains as a fraction of the frozen horizon, or a frozen per-endpoint tolerance
  such as the contract's `max_increase`; whether it should add per-family objectives; and
  whether the summary weights deserve a command-line option. None was needed to keep the
  report descriptive, so the most conservative choice was taken: no new parameter.
