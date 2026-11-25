---
"agent-harness-lab": minor
---

The harness profile now also checks the commands and paths in skills, slash commands, agent definitions and hook handlers against the project, with the same findings, ids and rules as the instruction files; a hook command is judged only after `$CLAUDE_PROJECT_DIR` is expanded.
