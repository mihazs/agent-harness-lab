---
"agent-harness-lab": minor
---

Add `agent_harness_lab/sources/config_inventory.py`, a presence-only inventory of runtime configuration. It covers Claude `.mcp.json`, settings files and output styles, Codex `.codex/config.toml` MCP servers and instruction-replacing keys, and memory locations. Rows hold only vocabulary labels, booleans, counts and digests, so command, argument, URL, env, header and token values are never recorded. Memory locations are `private_excluded` and are never opened. Roots outside the checkout are read only when declared by role. Codex project servers stay trust `unknown` unless the project is declared trusted. MCP server digests match `telemetry_server_sha256` for `mcp__` tool names. `surfaces.RULES` classifies these paths as `recommendation_only` or `private_excluded`, and the surface walk parses them as `config` nodes.
