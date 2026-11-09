---
"agent-harness-lab": patch
---

Move the plugin recommendation, precision solver, preservation, private and promotion journal, project sync, published release, refinement input and regression admission, control and proposal tests to pytest fixtures, plain asserts and parametrized cases, keeping every assertion. `PreservationCohortTests`, `PreservationHistoryTests`, `PreservationReceiptTests`, `RegressionAdmissionTests`, `RegressionControlTests` and `make_draft` keep the interface other test modules build on. A new repository test runs the standard-library discovery of the unittest-bound files with pytest blocked, so a helper module they import that takes pytest directly fails the suite.
