---
"agent-harness-lab": minor
---

Add `agent_harness_lab/config_recommendations.py`, a recommendation-only report built from config inventory and harness inventory documents. It flags shadowed Claude MCP servers, duplicate Codex servers (precedence `unverified`), `.mcp.json` servers that no user settings approve, Codex servers in a project layer that is not declared trusted, and output styles without `keep-coding-instructions: true`. For each Codex `.rules` file it suggests a `codex execpolicy check` command for the user to run. Memory locations are reported only as present and excluded. The report opens no file, runs nothing and writes nothing. Text comes from fixed templates and facts are only labels and digests, so no secret value can appear. Every row has `adoption_authority: false` and `publication_allowed: false`.
