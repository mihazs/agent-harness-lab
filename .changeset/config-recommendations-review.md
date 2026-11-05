---
"agent-harness-lab": patch
---

Config recommendations now refuse an inventory whose `version` is not a label, a `declared` inventory whose version differs from `surfaces.PINS`, and a digest field that is not a SHA-256 value. No digest covers these fields, so a forged value could previously reach the report. `mcp_server_shadowed` now records `winner_approval` and is `unverified` when the winning `.mcp.json` server is rejected or not approved by user settings. The memory exclusion test now records `dir_fd`-relative opens, with a positive control.
