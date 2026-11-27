---
"agent-harness-lab": minor
---

Old run outputs are deleted automatically. When `ahl improve` creates a run in its state directory, earlier `run-*` folders of that project are removed if they are older than `AHL_RUN_RETENTION_DAYS` (default 30). The newest `AHL_RUN_KEEP` (default 10) are kept, as is any run named by the incremental pointer or record. `AHL_RUN_RETENTION_DAYS=off` disables it, and an explicit `--output` is never touched. `scripts/isolated_project_check.py` now also removes its own leftover check folders older than a day and their registered worktrees before it starts.
