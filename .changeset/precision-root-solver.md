---
"agent-harness-lab": minor
---

`ahl experiment --preflight` now reports a descriptive `precision-sample-size-v1` plan: the minimum total of independent roots, with one sufficient allocation across families, that the frozen weighted-root Hoeffding method needs to meet the plan's half-width target and criteria, or an explicit infeasible reason for the task cap, trial ceiling, frozen cohort membership or a numerically unrepresentable target or alpha. Repeats never count as roots, and the plan never changes budgets, banks or adoption.
