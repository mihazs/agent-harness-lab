---
"agent-harness-lab": minor
---

`ahl experiment --preflight` now reports a descriptive `precision-sample-size-v1` plan: the minimum independent roots per family that the frozen weighted-root Hoeffding method needs to meet the plan's half-width target and criteria, or an explicit infeasible reason for the task cap, trial ceiling or frozen cohort membership. Repeats never count as roots, and the plan never changes budgets, banks or adoption.
