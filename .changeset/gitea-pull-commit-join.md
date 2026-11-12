---
"agent-harness-lab": minor
---

Gitea selection schema 6 emits a candidate evidence graph of server-reported pull request commits, and `sources.commit_join.join_pull_request_commit` relates one exact local Git commit to that listing offline. A match is server-reported membership under a declared repository fingerprint and the server's repository and PR IDs; it is not ancestry, merge result or outcome. Part of #46.
