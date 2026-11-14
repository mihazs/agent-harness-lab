---
"agent-harness-lab": minor
---

Let a user save a default harness scope for `ahl improve` and `ahl skills invoke` (part of #653): `ahl config set-scope global|project+global --agent codex|claude` writes an owner-only `user-scope.json` beside the remembered-runtime file (never in a project), `ahl config clear-scope` removes it and `ahl config show` reports it with the reason a file was ignored. A run that names no `--scope` uses the saved scope and, unless it passes `--agent`, the saved runtime; an explicit `--scope` (`project` included) always wins, and a corrupt, oversized, non-regular, foreign-owned or group/world-accessible file is ignored so the run stays on the project. Every receipt gains `scope_source` (`command_line`, `user_configuration` or `default`), and a blocked saved-default run names `--scope project` and `ahl config clear-scope` as the ways out. A project `ahl.json` still cannot select a scope, and the user-level layer is still changed only by `--repair-contracts`.
