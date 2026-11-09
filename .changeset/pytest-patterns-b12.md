---
"agent-harness-lab": patch
---

Move the GitHub window, GitLab, graph, content grouping, harness binding, Hindsight, hooks inventory, instruction, lineage, MCP and Mem0 source tests to pytest style. Each former `subTest` case is now its own parametrized test, `setUp` and temporary directories become fixtures, and no assertion site is dropped. `GitHubFixture` users in the window and graph modules run through the new `tests/support/github_case.py` host, `MCPReaderTests` keeps its `setUp()` and `doCleanups()` interface for the memory capture tests, `graph_for`, `SyntheticHarnessRepository` and `git_available` are unchanged, and the lineage and grouping fixtures that drive a `unittest.TestCase` by hand release it with `run_cleanups`.
