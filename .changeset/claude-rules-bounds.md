---
"agent-harness-lab": patch
---

Bound hostile Claude path-scoped rules. A pattern of more than 64 path segments is now `blocked`, brace groups are found in one linear scan, and glob matching no longer recurses, so a rule file can no longer raise `RecursionError` or burn minutes of CPU during capture or inventory. A `.md` file below `.claude/rules/` is now kind `path_rule` even when its path contains a `skills/` directory, so such rules are no longer dropped from the rules receipt.
