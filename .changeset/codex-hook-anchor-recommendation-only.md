---
"agent-harness-lab": minor
---

Document that Codex hook changes stay recommendation-only. At the pinned `openai/codex@d786ff91` source, Codex runs project hook commands in the session working directory and sets no project-root variable, so the hook contract keeps blocking every Codex unit. The source contracts cite the files and lines, and new synthetic tests pin that no Codex command form gets the project-root anchor.
