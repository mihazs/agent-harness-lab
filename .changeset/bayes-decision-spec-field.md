---
"agent-harness-lab": minor
---

Add the optional frozen `bayes_decision` field to `ExperimentSpec` for the planned paired Bayesian decision score: a region of practical equivalence, a Dirichlet prior, the posterior cut, a regression margin, draw count and cross-check level are pre-registered with strict validation, a JSON schema and typed errors that name the field. Defaults resolve at construction and are part of the plan digest and every trial id; a plan without the field serializes, hashes and schedules exactly as before. No score is computed yet and the Hoeffding certification path and `automatic_promotion` are unchanged.
