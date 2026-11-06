---
"agent-harness-lab": patch
---

Move the diagnostic corpus benchmark, docs, duplicate hook, effective agents, evaluation foundation, evidence, experiment, grader budget, control and correction, harness change plan, harness inventory and history report tests to pytest fixtures, plain asserts and parametrized cases, keeping every assertion and selected test. `TaskBankTests`, `GraderCorrectionTests` and `InstructionFixture` keep the `setUp` and `doCleanups` interface that other test modules build on.
