---
"agent-harness-lab": minor
---

Add `agent_harness_lab.diagnostics.cascade.evaluate`, a library check for a second diagnostic stage on held-out declared roots (part of #14). It keeps the first prediction beside the final one and counts corrected errors, newly introduced errors (from correct and from abstained answers), resolved abstentions, severe misses, remaining review roots and measured cost and latency. A cascade that corrects some answers but breaks as many or more is rejected, as are net-error, severe-miss, review-capacity and cost limits supplied by a frozen policy. Unmeasured cost stays unknown, unknown labels are not negatives, and the report never claims a benefit or promotion authority. No existing command, schema or report changes.
