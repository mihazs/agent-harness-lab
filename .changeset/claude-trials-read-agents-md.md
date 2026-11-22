---
"agent-harness-lab": patch
---

Pure Claude trials now read the `AGENTS.md` files of the harness under test: Claude Code loads `CLAUDE.md`, not `AGENTS.md`, so with user settings excluded both arms ran without those instructions and every comparison of an `AGENTS.md` project was a tie. A trial-only `CLAUDE.md` importing `@AGENTS.md` is staged beside each `AGENTS.md` that has none, identically in both arms, hidden from `git status` and recorded as `agents_md_bridge`.
