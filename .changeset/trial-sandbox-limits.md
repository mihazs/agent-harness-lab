---
"agent-harness-lab": patch
---

Claude trials no longer leave the empty `.claude/.cc-writes` runtime markers in the graded workspace, and the evaluation author is told which git paths the trial sandbox makes read-only, so it never builds a task that needs a worktree, git config change, hook or submodule.
