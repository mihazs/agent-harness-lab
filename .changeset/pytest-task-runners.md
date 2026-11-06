---
"agent-harness-lab": patch
---

Run the Taskfile test tasks with pytest instead of `python -m unittest`, keeping each task's selected tests. Repeated unittest `-k` filters become one `or` expression, and filter variables such as `KERNEL_TEST_FILTER` now take a pytest `-k` expression. `test:evaluation:bank-qualification` stays on unittest because its wheel variant has no pytest, and the pytest-patterns adaptation lists the files that standard-library-only runners still execute.
