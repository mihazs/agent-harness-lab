---
"agent-harness-lab": patch
---

Move the test suite from `tools/tests` into a top-level `tests/` package with `tests/unit/<area>/`, `tests/integration/<area>/` and `tests/support/`, using absolute imports between test modules. pytest uses `--import-mode=append` so installed wheel and archive runs still load the installed package, and a guard test fails on any test module outside `tests/` (the evaluator's own checks stay beside its scripts). Collected tests and every Taskfile selection are unchanged; wheel and release archive contents are unchanged.
