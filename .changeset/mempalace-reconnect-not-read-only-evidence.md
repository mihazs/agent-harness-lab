---
"agent-harness-lab": patch
---

Stop the MemPalace capability check from counting `mempalace_reconnect` in `advertised_write_tools`: the pinned server keeps it listed in read-only mode, so its presence is not evidence that the server was not read-only. It stays in the refused state-changing tools. Part of #50.
