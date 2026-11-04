---
"agent-harness-lab": minor
---

Add a frozen schema v2 multi-class diagnostic triage policy in `agent_harness_lab.diagnostics.triage_policy`. Validation reports a root-grouped class-wise confusion matrix, per-class TP/FP/FN/TN with abstentions, abstention reasons, a label-free review queue bounded by the declared review capacity, explicit per-error-type costs, and severe misses as a separate count with hashed root IDs. It never selects thresholds. It refuses thresholds that are not human-preregistered and roots listed as fitting, calibration or threshold-tuning roots. Results stay `rejected` or `insufficient_evidence`, descriptive only, and private to the benchmark exporter. Schema v1 binary policy output is unchanged.
