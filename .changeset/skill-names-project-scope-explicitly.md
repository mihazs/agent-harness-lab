---
"agent-harness-lab": patch
---

The `harness-self-improvement` skill now runs `ahl skills invoke . --scope project` for a request that names no scope, instead of leaving the flag out (part of #653). With a saved default scope (`ahl config set-scope`), the omitted flag made the skill run on the user-level harness although the user had named none; the command line now states the scope the user's words mapped to. Behavior of `ahl improve` and `ahl skills invoke` is unchanged.
