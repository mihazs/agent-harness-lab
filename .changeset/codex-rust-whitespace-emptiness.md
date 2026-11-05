---
"agent-harness-lab": patch
---

The declared Codex instruction inventory now decides whether a project or `CODEX_HOME` instruction file is empty with Rust `str::trim` semantics, as `openai/codex@d786ff91` does in `codex-rs/core/src/agents_md.rs` and `codex-rs/codex-home/src/instructions/mod.rs`. Python `str.strip()` also removed U+001C to U+001F, so a file containing only those separators was recorded as `selected_empty` with no byte budget, although Codex loads it and charges its bytes. Such a file is now content: it is declared applicable, counts against `project_doc_max_bytes`, and a budget it exhausts blocks the receipt.
