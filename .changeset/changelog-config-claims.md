---
"agent-harness-lab": patch
---

Correct the 0.113.0 and 0.114.2 changelog entries to the shipped config inventory: `.mcp.json` and output styles are `config` nodes, `.claude/settings.json` and `.codex/config.toml` keep the `hook` kind and are read by the config parser through multi-surface dispatch, and `.claude/settings.local.json` is presence-only and never read.
