# Frozen-plan precision preflight

Before a repeated comparison, inspect the exact selected plan without running
workers, reading provider credentials or calling a model:

```sh
ahl experiment --spec /path/to/plan.json --preflight
# A cohort-pinned plan also requires the exact original manifest bytes:
ahl experiment --spec /path/to/plan.json --cohorts /path/to/cohorts.json --preflight
```

The private JSON report describes the existing fixed-final, weighted-root
Hoeffding method. It includes selected tasks, declared roots, repetitions, frozen
family weights and per-root weights, Bonferroni allocation, primary/family/required
cohort half-widths, and the observed paired effect needed to cross each margin.
Required cohort membership is read from bytes matching the plan's SHA-256; this
numeric command does not authenticate its qualification declarations.

For example, 100 equally weighted roots in one family at total alpha .05 have
half-width about .2960414 after the primary/family allocation. Repeating each
root 20 times leaves that bound unchanged. Related tasks in one root also remain
one root. Sparse families and concentrated weights can dominate uncertainty.
A 1000-task cap applies; the command does not discover more independent roots,
add trials, relax margins or switch methods.

A plan may freeze `precision_half_width` in `(0, 1]`. This target applies to the
primary **and every required family and cohort endpoint**. Its addition changes
the plan hash and all scheduled trial IDs. The runner refuses an explicit target
when the half-widths, root minima or best possible bounded outcomes cannot meet
the frozen criteria, before sandbox probing, credential reads or provider calls.
It also respects the actual caller trial ceiling. The report is written as
`precision-preflight.json` beside the ordinary private results, and included in
`precision_preflight` in those results. If an inherited deadline is already
exhausted, the existing budget-exhausted report is returned without doing the
precision calculation or dispatching any worker.

Every preflight reports `root_similarity_check`. An unpinned legacy plan reports
`not_checked` and warns that declared roots may include related tasks; the command
cannot infer a bank from task IDs. A pinned plan reports `checked_heuristic` only
after rechecking its evidence, and an absent match still does not establish
independence. This warning does not change the frozen plan or legacy execution
behavior. To prepare a new bound plan before trials, follow the
[private root-similarity preparation](content-grouping.md#suspected-root-similarity)
procedure; previously collected trial IDs cannot be reused. This reporting
change also changes the analysis engine identity: explicitly regenerate controller
decision receipts, and do not cold-resume sequential runs pinned to an older engine.

Without a target, legacy descriptive execution remains available even when the
report says `infeasible`: this means its selected design cannot meet the frozen
fixed-analysis criteria, not that collecting descriptive observations is forbidden.
Omitting the optional target preserves existing plan hashes and trial IDs. The
standalone command exits 0 for a feasible design and 3 for an infeasible design;
invalid inputs are errors.

## Minimum independent roots

The same report includes `sample_size_plan` (profile
`precision-sample-size-v1`). It inverts the frozen method: for the plan's
alpha allocation, margins, root minimum, family weights, optional
`precision_half_width` and repeats, it returns the minimum declared roots per
family, or `infeasible` with explicit reasons. Each candidate size is accepted
only by the same endpoint code that computes the reported half-widths, so a plan
resized to the solution passes this preflight and removing any one root does not.
The total is minimal: an equal-weight family endpoint fixes each family's floor,
then roots are added to the family with the largest reduction of the primary
weighted sum until the primary endpoint and root minimum are met.

For one family at total alpha .05, a .1 target needs 877 roots; 876 roots leave
the half-width above .1. With weights .8/.2, effect margin .5 and no target, the
primary endpoint needs 30 and 9 roots. No variance estimate is used: the Hoeffding
range bound on paired root means in `[-1, 1]` is at least as conservative as a
worst-case Bernoulli(p=0.5) outcome. Repeats never become roots; the solved roots
are unchanged by repeats, which only multiply the minimum planned trials (one task
per root at the frozen repeat count). Reasons include a family or total over the
1000-task cap, a trial ceiling below the minimum trials, and a frozen required
cohort with too few members. Frozen cohort members raise their family floor and
are never reselected.

The plan is descriptive only. It does not add roots or trials, mutate the bank or
plan, change budgets, verify root independence, establish oracle authority or
adopt anything; `publication_allowed` is false and the profile is excluded from
public export. Resizing requires a newly frozen plan with new admitted roots and
a new hash. This supplies the sizing part of issue #8's precision-plan criterion;
independent curation, semantic oracle authority and the rest of #8 remain open.

This is analytic precision, **not statistical power**, verified root independence,
current bank eligibility, observed improvement or adoption authority. Wall-clock
feasibility is unknown without a preregistered execution-cost model. Unselected
bank availability and real-world generalization remain unknown. Reports stay
private, and fixed-final bounds cannot be repeatedly peeked at as a sequential
stopping rule. The anytime-valid sequential profile is described separately, and
model-based power is the simulation below; issue #9 is not closed by this preflight.

## Simulated power under a declared outcome model

Power needs an outcome model, which the analytic preflight never assumes. Declare
one explicitly and simulate the unchanged fixed-final decision engine:

```sh
ahl experiment --spec /path/to/plan.json --power-model /path/to/model.json \
  --simulations 200 --simulation-seed 0
```

The model is a JSON object with `schema` `synthetic-paired-outcome-model-v1`, a
`baseline_success` probability and an additive `candidate_effect` in `[-1, 1]`.
Optional fields are `family_effects` (per frozen family, overriding the common
effect), `root_spread` (each root's baseline success is drawn uniformly within
this distance of the mean, then clipped to `[0, 1]`), `repeat_correlation` (the
probability that a trial reuses its root's one shared draw for that arm instead of
an independent draw) and `critical_regression_probability` (per candidate trial).
Unknown fields, booleans, nonfinite or out-of-range values and unknown families
are rejected.

Each simulation draws one synthetic `TrialRecord` per scheduled trial and passes
the full set to `statistics.analyze`, so roots, repeats, Bonferroni allocation,
family noninferiority and critical-regression blocking behave exactly as in a
recorded analysis. The private report (profile `fixed-root-power-simulation-v1`)
gives each disposition's count, rate and Wilson 95% Monte Carlo interval, the
reason counts, the plan and model SHA-256, declared roots separately from
repetitions, and the simulated trial count. `supported_rate` is labeled `power`
for a beneficial declared effect, `false_recommendation_rate` for a null or
harmful one, and `mixed_family_effects` otherwise. Plans with a cohort pin or a
sequential profile are refused, and one call is capped at 10000 simulations,
100,000 planned trials per simulation and 2,000,000 simulated trials, checked
before any schedule is built. A completed simulation exits 0.

The offline tests predeclare their criteria before running: 300 null simulations
with heterogeneous roots and correlated repeats keep the Wilson upper bound of the
false-recommendation rate at or below .05, a large benefit has a Wilson lower bound
of power at or above .9, a small effect with 100 roots stays inconclusive, known
or one-family harms are rejected and any critical regression rejects a large
benefit. These checks hold for the synthetic model only.

A separate fixed-interval check draws 100 paired root differences of −1 or +1
with true mean zero, 1,000 times with seed 394. With independent roots, the
Bonferroni-adjusted interval covered zero in 996 draws. When all roots instead
share one family-level draw, it covered zero in 0 draws: the nominal bound does
not apply to that dependent design. These observed rates are synthetic Monte
Carlo results, not coverage guarantees for real tasks. The interval also does
not account for selecting among candidates; candidate-count multiplicity is
separate work.

When fixed-final analysis has missing or ungraded scheduled arms, it remains
incomplete and cannot promote. It reports an identified range for the frozen
family-weighted mean of paired root differences, plus pessimistic and optimistic
Hoeffding limits. These are worst- and best-case bounds over unknown binary
outcomes, not assigned grades; the point estimate is unknown. The sampling limits
still assume independent root means and do not account for candidate selection.
For compatibility, an entirely empty record list has the legacy incomplete
response; callers can request its full-range bounds with `include_empty_bounds=True`.
If the adjusted confidence calculation is numerically unrepresentable, its outer
limits fall back to the full range and report `conservative_fallback`.
Preservation analysis also reports bounds for each required frozen cohort, using
equal selected-root weights and the required-cohort Bonferroni allocation. It
does so only with valid frozen membership and unambiguous records; qualification
and independence remain unverified. Optional cohorts remain descriptive. Sequential
analysis does not yet provide inferential missing-outcome bounds. Its numeric API
can opt in to a descriptive finite-bank completion range for a matching partial
prefix; unobserved arms remain unknown and no confidence-sequence or promotion
claim follows. This range is unavailable through the CLI and is not a replayable
controller inspection.

The power simulator's roots are independent by construction, so its result says nothing about
real task dependence. No worker, model or grader runs, nothing is observed,
certified or adopted, and the plan, margins, method and budgets are unchanged. A
low supported rate is not evidence of noninferiority. The report stays private
(`publication_allowed` is false) and `automatic_promotion` remains false.

For an optional frozen root-similarity check, see
[Suspected root similarity](content-grouping.md#suspected-root-similarity). When
candidate roots are present, the preflight combines their weights for each
endpoint; the reported effective count is a heuristic planning count and does not
verify independence. A pinned plan needs both the original evidence and its
current bank even when no candidates were found. Evidence-bound execution also
fails closed when the effective-root criteria cannot be met without a precision
target.
