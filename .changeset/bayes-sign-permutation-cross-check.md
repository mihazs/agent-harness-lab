---
"agent-harness-lab": minor
---

Add `evaluation/sign_permutation.py`, the exact paired sign-permutation cross-check for the planned Bayesian decision score: a one-sided test on the per-root differences shifted by `rope_epsilon`, weighted by frozen family weights, exact up to 20 nonzero roots and a seeded Monte Carlo test above (labeled `monte_carlo`), plus a comparison that can only downgrade a `better` or `worse` quality verdict to `insufficient_evidence` with reason `cross_check_disagrees`. It is a pure module with no model call, no I/O and no new dependency; nothing is wired into `ahl experiment`, `ahl improve` or `ahl compare` yet, and the Hoeffding path and `automatic_promotion` are unchanged.
