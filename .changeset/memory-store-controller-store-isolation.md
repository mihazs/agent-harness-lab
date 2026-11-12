---
"agent-harness-lab": minor
---

`ahl memory init` now refuses a location inside any existing controller store (a directory with a `.store.json` marker) without needing `--exclude`, and a controlled evaluation store that overlaps the directory named by `AHL_MEMORY_STORE` is refused before any read. `ahl memory status` reports whether SQLite FTS5 is available (`null` when the probe failed). `gc` accepts `--dry-run` (planning stays the default) and every subcommand accepts `--json`. Part of #632.
