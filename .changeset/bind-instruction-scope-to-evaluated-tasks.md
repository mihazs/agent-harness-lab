---
"agent-harness-lab": patch
---

Bind the governing scope of scoped instruction and rule rewrites to the evaluated tasks instead of the proposer's plan alone (#339). After the evaluation manifest is validated and before baseline or candidate trials run, `instruction_validation.bind` checks the plan's `task_cwd` against the task `cwd` values, where a task without one runs at the root, and checks that each task path lies below one of them. A plan that declares an unrelated `task_cwd` to reach a sibling `CLAUDE.md`, `AGENTS.md` or rule is refused as `instruction_validation_blocked` (`task_cwd_not_evaluated`, `task_path_not_evaluated`). The report records `scope.source: "evaluation_manifest_task_cwds"` and `scope.task_cwds`, `check_scope` enforces the bound scope, and `current` reports `scope_unbound` for a scope that was never bound, so no recommendation rests on a proposer-declared scope.
