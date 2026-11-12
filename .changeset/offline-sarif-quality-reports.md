---
"agent-harness-lab": minor
---

Add an offline `local_file/quality-reports/v1` source that imports selected SARIF 2.1.0 logs bound to a caller-declared full commit SHA. Each result becomes a hash-only `quality_finding` development-artifact record that keeps SARIF's own `level`, `kind` and suppression marker apart from test statuses and carries no outcome, so a static-analysis or security finding is never a test failure or a task grade. Absent, unrecognized or contradictory levels stay `unknown`, a log without findings or without a reported successful tool run is flagged as not clean, and a run with external property file references is refused. Messages, code flows and URIs are never stored, followed or fetched, and profile `ci-quality-reports-v1` is rejected by public export. `ahl sources preview` reports a `quality_reports_summary`. Part of #53: TAP, coverage and lint reports, artifact download and live expiry warnings remain open.
