# Repeat reliability profile

Reference for the `repeat_reliability` object that a fixed-sample analysis returns beside
its decision fields. It answers a question a mean lift cannot: does an arm pass a root
every time, or only some of the time, and is the worst root worse than the average?

```sh
ahl experiment --spec plan.json --records trials.json   # prints repeat_reliability
```

The CLI always includes it for a recorded fixed analysis. The Python function
`agent_harness_lab.evaluation.statistics.analyze` returns it only with
`include_reliability=True`: sealed controller histories, decision receipts and
`ahl improve --experiment-request` keep their pinned legacy analysis bytes and do not
carry it.

## When it is present

The profile is added when the recorded trials are the exact planned set, every trial is
graded by an executable grader with matching provenance, and no critical regression
vetoes the run. It then accompanies a `supported`, `inconclusive` or `rejected`
(correctness) result, including an `inconclusive` result caused by too few independent
roots. It is absent for incomplete, blocked and critical-regression analyses, for
cohort-pinned analyses and for sequential analyses: an ungraded or missing trial is
never treated as a pass or a failure to fill it in.

## Fields

`overall` summarizes every selected root; `families` repeats the summary per frozen
family. Every figure is computed per root and then counted or compared across roots,
so repeats and related tasks stay inside their root and never become extra samples.

| Field | Meaning |
| --- | --- |
| `roots` | Selected roots in the group |
| `consistency` | `measured` when the plan repeats each task at least twice, else `not_measurable_single_repeat` |
| `<arm>.all_pass_roots` | Roots where every recorded trial of that arm passed |
| `<arm>.all_fail_roots` | Roots where every recorded trial of that arm failed |
| `<arm>.lowest_root_success` | Smallest per-root pass fraction (the worst root) |
| `<arm>.repeat_unstable_roots` | Roots where some task both passed and failed across its repeats; `null` when consistency is not measurable |
| `paired.worst_root_difference` | Smallest per-root mean of candidate minus baseline |
| `paired.best_root_difference` | Largest per-root mean of candidate minus baseline |
| `paired.negative_difference_roots`, `paired.positive_difference_roots` | Roots where the candidate did worse or better on average |

`<arm>` is `baseline` or `candidate`. A root with several tasks counts as unstable only
when one task disagrees with itself across repeats; tasks that simply differ from each
other do not.

## What it does not claim

The profile is a description of recorded outcomes. It has no interval, no power claim and
no independence claim, and it is not an input to the promotion policy, the weighted-root
bound or any receipt disposition. Variation across repeats can come from the model, the
grader or the environment; the profile does not say which. Two candidates with the same
mean lift can differ widely here, so read it together with the interval and the family
results, not in place of them. `automatic_promotion` stays false.

For why many designs return `inconclusive`, see
[Inconclusive is an expected result](experiment-precision.md#inconclusive-is-an-expected-result).
