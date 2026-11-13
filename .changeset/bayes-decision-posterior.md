---
"agent-harness-lab": minor
---

Add a pure Bayesian decision function beside `statistics.py`: a seeded Dirichlet posterior over paired root differences with a region of practical equivalence, the score `S = P(delta > epsilon)`, the `better`, `equivalent`, `worse` or `insufficient_evidence` decision against the frozen `p_cut`, root and precision floors, worst-case and best-case bounds for ungraded outcomes that never count as failures, and the lexicographic gate. It is not wired into any command; the Hoeffding certification path and `automatic_promotion` are unchanged.
