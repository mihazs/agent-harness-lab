---
"agent-harness-lab": minor
---

Add an opt-in interaction contract for grading the evaluated task agent's clarification, deliberate stop and no-change behavior (#123). `evaluation.interaction_contract` validates a versioned contract with a stored digest and grades observed evidence into separate three-valued `interaction_requirement_satisfied`, `whole_task_completed` and `state_preserved` results, so asking at a declared boundary no longer implies a finished task. Crashes, timeouts, missing transcripts or receipts and unsupported continuation stay unknown or failed, never a stop. Existing artifact-only tasks and graders are unchanged; the evaluator is not yet wired to the contract.
