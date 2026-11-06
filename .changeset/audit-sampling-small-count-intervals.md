---
"agent-harness-lab": patch
---

Replace the normal (Wald) intervals in `diagnostics.audit_sampling` with intervals that keep nominal coverage when only a few incidents are sampled (#386). Each stratum now gets the exact binomial-index analogue of the Clopper-Pearson interval under the Poisson design. A stratum with no sampled case adds its zero-event upper bound instead of a certain zero, and the limits combine across strata by MOVER. Triage recall applies MOVER to the log ratio of missed to alerted incidents. Counts and rates are marked `interval: exact_binomial_mover` and recall `interval: exact_binomial_mover_log_ratio`. The zero-event bounds for estimates with no sampled case are unchanged. Receipts now list the `multi_stratum_intervals_approximate` limitation in place of `normal_approximation_intervals`. Exact-enumeration tests with 1 to 5 sampled incidents keep two-sided coverage within tolerance of 95%, where the Wald interval failed 11 of the 16 cases.
