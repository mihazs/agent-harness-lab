---
"agent-harness-lab": patch
---

Move the regression replay and review, release install, repeated workflow, review authority, run-queue gate, runtime boundary and sandbox cgroup, construction, deadline, grader, image and inference tests to pytest fixtures, plain asserts and parametrized cases, keeping every assertion. `RegressionReplayTests`, `ReviewFixture`, `PublicFixture`, `ControlledWorkflowTests`, `SandboxedGraderTests`, `ControllerInferenceTests` and `ResponsesEvaluationTests` keep the interface other test modules build on, and now release their nested helpers with `run_cleanups`, so a failed nested cleanup is no longer dropped. `test_sandbox_inference` takes pytest from `tests/support/optional_pytest.py` because the unittest-run derivation falsifier imports it, and the fixture cleanup probe covers the new fixtures, including the repeated-workflow kernel scopes.
