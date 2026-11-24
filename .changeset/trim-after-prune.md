---
"agent-harness-lab": patch
---

The trim of unmeasured rules applies once per run on any measured attempt, so a candidate that already went through the prune and still carries a rule no task measured is trimmed instead of refused whole. A pruned, refined or trimmed candidate is judged on the evaluation that was last built, not on an earlier claim's tasks. Codex trials get a repository fixture's git directory outside the workspace and granted as writable, since the sandbox keeps a workspace's own `.git` read-only. An empty `.git` directory, which the Codex sandbox leaves in the temporary directory, no longer counts as a source checkout.
