---
"agent-harness-lab": patch
---

Move the source context, contract, controller, dispatch, Git, Gitea, GitHub (core, commits, details, threads, live), execution and frontmatter tests to pytest style. Each former `subTest` case is now its own parametrized test, `setUp` and temporary directories become fixtures, and the helpers that other test modules import keep their interfaces: `GitHubFixture` stays a plain mixin for the unittest-style GitHub modules, with `GitHubCase` as its pytest host, `SourceDispatchTests` keeps its `setUp()` and `doCleanups()` for the normalized export tests, and `metadata_for` and `Response` are unchanged. The Taskfile selectors follow the renamed `TestControllerSource` and `TestGitHubLive` classes.
