---
"agent-harness-lab": minor
---

The static harness audit now follows the Codex project-doc budget over the `AGENTS.md` directory chain for `task_cwd`, as the pinned `openai/codex@d786ff91` source loads it: the file that crosses the default 32 KiB `project_doc_max_bytes` budget is `nonconformant` (`instruction_chain_truncated_at_default_project_doc_bytes`) and each later non-blank file is `nonconformant` (`instruction_chain_skipped_at_default_project_doc_bytes`). Findings gain `measures` with the budget, loaded and dropped byte counts, so the finding names the crossing file and what Codex drops. A chain that fits adds nothing, and the chain is not judged when the checkout root has no `.git`, the project is declared untrusted, `task_cwd` is unknown, or a candidate is refused or unparsed. A configured budget is never read. The Codex instruction parser records `codex_leading_blank_bytes` to follow Rust's blank-file rule.
