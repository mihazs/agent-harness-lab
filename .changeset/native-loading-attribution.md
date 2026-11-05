---
"agent-harness-lab": minor
---

The pinned Claude Code 2.1.316 native profile now records `nested_memory` and hook-output attachments, `Agent`/`Task` `subagent_type` requests and `mcp__<server>__` tool prefixes as content-free `loading_signal` metadata (path, content, agent-name and MCP-server digests). These attachment shapes are unverified and backed only by synthetic fixtures. `harness_inventory.join_loading` joins them to inventory nodes. A node's `observed_loading` becomes `observed_pattern` only when both path and content hash match exactly, and `effective` stays `unknown`. Evidence for a path or name the inventory lacks is counted as unattributed. A missing capture leaves every node `unknown`, and Codex inventories record `no_native_loading_signal`.
