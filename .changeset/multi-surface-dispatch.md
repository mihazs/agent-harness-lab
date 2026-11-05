---
"agent-harness-lab": patch
---

The harness inventory now offers `.claude/settings.json` and `.codex/config.toml` to every parser whose kind is among the file's classifier facets. Other files with a second facet, such as a `hooks/hooks.json` inside a skill bundle or an agent definition's hook facet, are still read by their primary parser only. The hook parser keeps the node's `parse_status` and plain facts. The config and new plugin-enablement parsers each record namespaced `config.*` and `plugin.*` facts with their own `parse_status`, so a failure in one parser no longer hides the others. The plugin parser records counts only, so plugin ids, marketplace locations and secret values never reach a node.
