---
"agent-harness-lab": minor
---

Gitea selection schema 6 emits a candidate evidence graph of server-reported pull request commits, and `sources.commit_join.join_pull_request_commit` relates one exact local Git commit to that listing offline. A match is server-reported membership under a declared repository fingerprint and the server's repository and PR IDs; it is not ancestry, merge result or outcome, and it requires the graph's `commit_on` edge between the PR-commit and PR records, so a `pull_id` and `number` naming different selected PRs stay `unknown`. Part of #46.
