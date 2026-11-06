---
"agent-harness-lab": patch
---

Move the offline evaluation, onboarding, one-call, optimization history, oracle and permission audit tests to pytest fixtures, plain asserts and parametrized cases, keeping every assertion. `OfflineFixture`, `OracleHistoryTests`, `OracleBankAuthorityTests`, `OraclePlanTests`, `OneCallTests` and the oracle behavior `fixture` keep the interface other test modules build on. Modules that the standard-library unittest runners import for helpers now take pytest from `tests/support/optional_pytest.py`, which stands in with pass-through decorators when pytest is not installed.
