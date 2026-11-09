---
"agent-harness-lab": patch
---

Move the sandbox kernel, resource, storage and working-state, scope receipt encoding, search history, sequential and sequential kernel, skill invocation, agent inventory and Claude source tests to pytest style. Each former `subTest` case is now its own parametrized test, `setUp` and temporary directories become fixtures, and the helpers that other test modules import keep their interfaces: `KernelFixture` and the unbound kernel test methods stay callable from the unittest-bound bank qualification tests and the permission composition tests, and `ClaudeSourceTests` keeps its `setUp()` and `doCleanups()`.
