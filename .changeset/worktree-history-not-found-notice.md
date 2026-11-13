---
"agent-harness-lab": minor
---

Tell the user when session history is missing only because the project is a linked Git worktree. `ahl analyze` and `ahl improve` of a worktree whose own path has no captures now report `history_not_found_for_worktree` (a `capture_reasons` entry, an `ahl improve --history` blocked reason, and a one-step remediation) instead of an empty success; a path whose repository files cannot be read stays unknown and gets no worktree claim. The new opt-in `--history-from CHECKOUT` reads the sessions recorded for another checkout of the same repository (same Git common directory, read from files without running Git; unrelated directories are refused) read-only and labels the receipt `source_project_path_differs`. Sessions are never merged silently and the option excludes `--events` and `--source-root`.
