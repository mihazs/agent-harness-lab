---
"agent-harness-lab": minor
---

GitLab selection schema 9 emits a candidate evidence graph of server-reported merge request commits, and `sources.commit_join.join_merge_request_commit` relates one exact local Git commit to that listing offline. A match is server-reported membership under a declared project fingerprint; it is not ancestry, merge result or outcome. Gitea reconciliation stays open. Part of #46.
