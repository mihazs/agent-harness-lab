---
"agent-harness-lab": minor
---

Record a caller-declared managed-policy and user `CLAUDE.md` as read-only influences in the declared Claude Code instruction inventory (`capture(..., claude_managed_root=..., claude_user_home=...)`). The folders are never discovered, defaulted or read from the environment, must be separate from the checkout and each other, and link, hard-link and `.git` paths fail closed. Each file gets a path-free `claude_managed` or `claude_user` node that is declared applicable only when non-empty, UTF-8, under the runtime size limit and not left out by a declared `managed-only` or `claude_md_excludes` setting (the managed file is never excluded), with `parent` edges managed, user, first project file; missing, empty, non-UTF-8, oversized and unreadable files stay unknown, and `@path` imports are never followed but leave an unknown marker. Observed loading stays unknown and nothing grants edit authority.
