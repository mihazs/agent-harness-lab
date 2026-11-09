---
"agent-harness-lab": patch
---

Move the surface, telemetry, trajectory assertion and evaluation, triage policy, unlimited analysis, updater, workflow (decision disposition, prerequisites, scripts improvement, core), hook validation and GitHub Actions source tests to pytest style. Each former `subTest` case is now its own parametrized test, `setUp` and temporary directories become fixtures, and the helpers that other test modules import keep their interfaces: `WorkflowTests` and `TrajectoryEvaluationTests` stay `unittest.TestCase` fixture hosts with `setUp()`, `FAKE` and the other helpers, and hand-driven hosts are released through `run_cleanups`. The GitHub Actions run row is stored as `run_row`, so it no longer shadows `TestCase.run`.
