---
"agent-harness-lab": patch
---

Publish every version commit from the release workflow even when other changesets are pending on main: an idempotent step after Changesets releases each version without a GitHub release, oldest first, from its own version commit, only with a matching CHANGELOG entry and a clean tree. Closes #677.
