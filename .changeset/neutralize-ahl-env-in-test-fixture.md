---
"agent-harness-lab": patch
---

The shared test fixture now removes `AHL_STATE_HOME`, `AHL_MEMORY_STORE` and `AHL_MANAGED_PREPARATION` from every test, so an exported override can no longer change a test result or receive writes from the suite; a guard test fails when product code reads an `AHL_*` variable the fixture does not neutralize. Test-only change. Closes #718.
