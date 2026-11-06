---
"agent-harness-lab": patch
---

New tests pin how the Codex instruction byte budget treats directories left `unknown` by a declared `project_root_markers` entry that the checkout does not contain. In such a directory the first existing candidate is selected. An empty `AGENTS.override.md` adds no bytes to `project_doc_max_bytes` and still keeps a large `AGENTS.md` beside it from being counted, so the `task_cwd` file fits a budget sized for itself alone. A non-empty selection is counted in place of the large file, and that includes one made only of the separators U+001C to U+001F. The source contract now spells out this rule. Behaviour is unchanged.
