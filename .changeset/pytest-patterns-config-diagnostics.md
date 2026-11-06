---
"agent-harness-lab": patch
---

Convert fourteen configuration, construction, controller, correction, derivation and diagnosis test modules from `unittest.TestCase` to pytest fixtures, plain `assert` and `pytest.mark.parametrize`, without dropping a case or an assertion. `task test:evaluation:history-identity` now selects `-k TestHistoryIdentity` and `task test:configuration:responses:kernel` selects `TestConfiguredResponsesKernel`, so both still run the same tests. The shared harnesses that other test modules build directly keep their `setUp` and `doCleanups` methods.
