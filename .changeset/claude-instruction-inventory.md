---
"agent-harness-lab": minor
---

Add a declared Claude Code 2.1.317 project memory inventory to offline Git captures. It fingerprints root and nested `CLAUDE.md`, `.claude/CLAUDE.md` and `CLAUDE.local.md` files, plus bounded `@path` imports up to the documented four hops, with missing, cyclic, external, out-of-cwd and depth-limited imports flagged explicitly. AGENTS.md shadowing is declared under the documented default setting only. Rules, on-demand subdirectory files and managed or user memory stay unknown, observed loading stays unknown, and nothing outside the checkout is read.
