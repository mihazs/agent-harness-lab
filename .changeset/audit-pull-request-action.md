---
"agent-harness-lab": minor
---

Part of #392: a composite GitHub Action (`action.yml`) runs the read-only `ahl audit --json` on a pull request checkout and prints `::warning file=` annotations for the findings, limited to changed files when two commits of history are available, bounded to 50 lines, with hostile file names and finding text escaped by `agent_harness_lab.audit.annotations`. The in-repo `harness-audit.yml` workflow uses `pull_request` only with `contents: read` and no secrets, and tests assert the permissions, the absence of `pull_request_target`/`workflow_run`, a reproducible no-write run, and the hostile-name converter. The Action is not yet published or tagged: that public-surface step needs maintainer authorization and the issue stays open for it.
