---
"agent-harness-lab": minor
---

`ahl analyze --source-root` now accepts a bounded Claude `projects` directory with the pinned `claude-jsonl-2.1.316` profile. It reads only the project's mapped directories through the source-journal root reader, selects an exact session UUID with `--session`, and reports reached limits, unattributable or unrecognized files and missing directories as coverage caveats.
