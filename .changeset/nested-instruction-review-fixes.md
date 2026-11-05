---
"agent-harness-lab": patch
---

Keep the whole-tree Claude Code instruction walk bounded and non-fatal. Files are hashed while streamed, so outside-scope and `CLAUDE.local.md` files no longer stay in memory, and an expanded file is re-read, at most 4 MiB of it. An unreadable subdirectory or instruction file becomes an `unknown` node (`instruction_directory_unreadable` or `instruction_file_unreadable`); the receipt is `blocked` only when it sits on or below the task directory. An on-demand import through a link, a nested repository or an unreadable path becomes an unresolved node instead of failing the capture. An oversized on-demand file is no longer skipped silently: following the documented truncation, its imports are still resolved, the file is marked `on_demand_truncation_unverified`, and imports past the smallest documented 40,000-character limit are `import_beyond_on_demand_truncation_unverified`.
