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

Without a target, legacy descriptive execution remains available even when the
report says `infeasible`: this means its selected design cannot meet the frozen
fixed-analysis criteria, not that collecting descriptive observations is forbidden.
Omitting the optional target preserves existing plan hashes and trial IDs. The
standalone command exits 0 for a feasible design and 3 for an infeasible design;
invalid inputs are errors.

This is analytic precision, **not statistical power**, verified root independence,
current bank eligibility, observed improvement or adoption authority. Wall-clock
feasibility is unknown without a preregistered execution-cost model. Unselected
bank availability and real-world generalization remain unknown. Reports stay
private, and fixed-final bounds cannot be repeatedly peeked at as a sequential
stopping rule. An anytime-valid sequential profile and model-based power planning
remain separate work; issue #9 is not closed by this preflight.
