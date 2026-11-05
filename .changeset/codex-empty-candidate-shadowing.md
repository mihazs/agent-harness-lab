---
"agent-harness-lab": patch
---

The declared Codex instruction inventory now selects each project directory's instruction file by existence, as `openai/codex@d786ff91` `codex-rs/core/src/agents_md.rs` does. The first existing regular-file candidate (`AGENTS.override.md`, `AGENTS.md`, then the fallback names) is selected whatever its content. An empty selection is recorded as `unknown` with reason `selected_empty`, contributes no guidance or byte budget, and shadows the directory's later candidates, so an empty `AGENTS.override.md` now hides that directory's `AGENTS.md` instead of falling through. An untrusted project or a directory above the project root now marks empty files `out_of_scope` too. `CODEX_HOME` keeps its first-non-empty rule.
