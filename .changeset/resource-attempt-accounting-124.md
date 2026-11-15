---
"agent-harness-lab": minor
---

Count retried, interrupted and resumed attempts in resource decisions. A paired row for `resource_decision.decide` or `success_cost` may now carry per-arm `attempts`; the arm's spend is their sum, so a resume can only add spend and a cheaper completed attempt cannot replace a dearer one. An attempt without a usable measurement, a malformed attempt list, a final status that differs from the supplied one, or a supplied total that is not the attempt sum makes the endpoint unknown (`attempt_measurement_missing`, `attempt_record_invalid`, `attempt_total_mismatch`), never zero or favorable. An optional row `trial` identifier must be a unique nonempty string; two rows for one trial are refused. The version 4 token cross-checks run on the sums. Rows without these fields behave as before. Part of #124.
