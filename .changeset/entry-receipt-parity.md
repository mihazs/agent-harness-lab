---
"agent-harness-lab": minor
---

Add a receipt parity contract (`ahl-entry-receipt-parity-v1`, `agent_harness_lab/entry_parity.py`) between `ahl improve PROJECT` and `ahl skills invoke PROJECT`. For the same project, frozen inputs and runtime, both routes return the same exit code, stdout receipt and `workflow.json` keys and values, including status, recommendation, the `decision` block, the change plan and prerequisites. The only allowed differences are the private run directory, digests that bind run-local timing or invocation context, measured durations and remaining budget, the `origin` and `parent_run_id` given to preparation children, and the skill-only `invocation.json`. Stderr matches apart from the run directory and elapsed times, and a managed-preparation marker denies both routes with identical output. Receipts, statuses, exit codes and gates are unchanged, and the contract grants no authority.
