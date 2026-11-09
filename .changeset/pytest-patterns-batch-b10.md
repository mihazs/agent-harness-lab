---
"agent-harness-lab": patch
---

Move the Claude and Codex native source, Claude instruction, rule and Claude-Mem, Cognee, command, commit-join and config inventory tests to pytest fixtures, plain asserts and parametrized cases, keeping every assertion. `NativeSourceTests` keeps the interface the other source tests build on, and the Claude root and commit-join fixtures release their hand-driven TestCase helpers with `run_cleanups`, so a failed nested cleanup is no longer dropped. The fixture cleanup probe covers both new fixtures.
