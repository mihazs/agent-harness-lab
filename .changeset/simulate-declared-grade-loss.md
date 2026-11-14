---
"agent-harness-lab": minor
---

The power-simulation outcome model (`synthetic-paired-outcome-model-v1`) accepts optional `baseline_ungraded_probability` and `candidate_ungraded_probability`. A hit trial becomes an unknown grade that is never imputed, so the fixed analysis returns `incomplete` for any simulation that lost one and the sequential controller ends at the first inspected root holding one; both profiles see the same lost grades. The private report adds a `completion` block (declared probabilities, mean ungraded trials, simulations that kept every grade with a Wilson 95% interval). Models and reports that omit the fields keep their bytes. Partial roots and time-budget exhaustion remain unsimulated, and the report still runs no worker, model or grader and cannot authorize promotion.
