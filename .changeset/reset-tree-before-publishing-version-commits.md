---
"agent-harness-lab": patch
---

Reset the release workflow checkout to the built commit before publishing unreleased version commits, because the Changesets step leaves its version edits in the tracked tree and the publisher refuses a dirty tree. Part of #677.
