---
"agent-harness-lab": minor
---

`sources.claude_mem_correlation.correlate_native_sessions` offline counts one session once across an imported Claude-Mem export and native Claude or Codex journals. An exact content-session identifier under the runtime named by `platform_source` yields a candidate receipt; duplicates, cross-runtime matches, unstated platforms and stale exports stay separate, and nothing is matched by time or content. Worker/MCP reads, SQLite snapshots and `tool_uses` stay open. Part of #48.
