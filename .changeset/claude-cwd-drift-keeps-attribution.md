---
"agent-harness-lab": patch
---

Keep a Claude session attributed to its project when later records change working directory. The launch directory decides attribution, later records are tagged with a content-free `cwd_class` and counted per class, registered worktrees are read from the repository without running git, and explicit and default discovery now both read such a capture with the verified adapter.
