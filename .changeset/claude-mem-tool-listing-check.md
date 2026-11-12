---
"agent-harness-lab": minor
---

Add `ahl sources claude-mem tool-check --tools FILE`, an offline check that compares one saved Claude-Mem MCP `tools/list` result with the pinned v15.9.0 `search`, `timeline`, `get_observations` and `get_tool_uses` input schemas and read-only hints. It never contacts a worker, calls a tool or prints descriptions and unrecognized tool names, and a `compatible` verdict does not authorize a read. Part of #48.
