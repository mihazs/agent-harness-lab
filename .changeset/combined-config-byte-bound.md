---
"agent-harness-lab": patch
---

Keep the 1 MiB runtime configuration bound in front of every decoder in multi-surface dispatch. The harness inventory now refuses a combined `.claude/settings.json` or `.codex/config.toml` over `MAX_CONFIG_BYTES` once, before any parser runs, recording `parse_failed` for the primary kind and every facet. The `config` parser also checks the bound itself, so an `.mcp.json` node over it is `parse_failed` too, and the hook, plugin and command parsers share the same constant.
