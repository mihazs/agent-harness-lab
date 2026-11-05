---
"agent-harness-lab": patch
---

The harness inventory now offers `.claude/settings.json` and `.codex/config.toml` to every parser whose kind is among the file's classifier facets. The hook parser keeps the node's `parse_status` and plain facts. The config and new plugin-enablement parsers each record namespaced `config.*` and `plugin.*` facts with their own `parse_status`, so a failure in one parser no longer hides the others. The plugin parser records counts only, so plugin ids, marketplace locations and secret values never reach a node.
