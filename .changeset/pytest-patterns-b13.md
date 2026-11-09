---
"agent-harness-lab": patch
---

Move the memory capture and provenance, native compatibility, OKF, permission composition, plugin and workflow inventory, recheck, store, temporal, test results, transport and starter bank tests to pytest style. Each former `subTest` case is now its own parametrized test, `setUp` and temporary directories become fixtures, and no assertion site is dropped. `SourceStoreTests` keeps its `setUp()` and `addCleanup` interface for the lineage and chunked read fixtures, `Response` in the transport module is unchanged, the recheck tests run through `tests/support/github_case.py`, and the permission composition cases release their hand-driven `unittest.TestCase` hosts with `run_cleanups` and `run_class_cleanups`.
