---
"agent-harness-lab": minor
---

Add triage evaluation profile v8 (`diagnostic-triage-evaluation-v8`, part of #74). `evaluate(..., report_version=8)` applies every v7 check, decision and diagnostic unchanged and adds `population_metrics` over labeled declared roots: per-severity TP/FP/FN/TN, precision and end-to-end recall, plus alert precision, incident recall (abstained incident roots stay in the denominator) and the accepted-negative error rate. A metric with no root behind it is null. `metric_scopes` labels the per-class entries as class-conditional and the new block as population-level. The values are descriptive and feed no constraint, so a high population recall cannot rescue a failed class recall. The report stays private with no certification or promotion authority; earlier profiles and `ahl benchmark` are unchanged.
