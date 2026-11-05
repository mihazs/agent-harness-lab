---
"agent-harness-lab": patch
---

Fix the config inventory found in review. A deeply nested `.codex/config.toml` is now `parse_failed` under the same depth bound as JSON, instead of a `RecursionError` that crashed the inventory and the surface walk. A settings file at a runtime path is always inventoried, even when it carries AHL's derivation `profile` key; AHL's derivation snapshot stays excluded by its path alone. The memory audit test now also detects reads by path string.
