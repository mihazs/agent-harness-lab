---
"agent-harness-lab": patch
---

Move the certification access, chunked read, Claude and Codex capture, CLI help, surface smoke, closed project input, cohort requalification, collection progress, commit message and configured execution tests to pytest style. Each former `subTest` case is now its own parametrized test, `setUp` and temporary directories become fixtures, and the capture builders that other test modules import keep their `setUp()` and `doCleanups()` interface.
