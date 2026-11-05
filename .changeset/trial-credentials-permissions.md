---
"agent-harness-lab": patch
---

A Codex CLI evaluation now blocks as `codex_credentials_unavailable` before any trial when the binary reports the `codex-cli` banner and `CODEX_API_KEY` is not set, because the credential-free temporary `CODEX_HOME` cannot reach a saved `codex login`. `results.json` records `runtime.codex_authentication` (`environment_variable_unobserved`, `unavailable`, `unknown` or `inherited`). The Claude Code trial descriptor now lists user and local permission rules, sandbox settings, `env` and `apiKeyHelper` as excluded by `--setting-sources project`, and inherits only project and managed permissions. The execution reference, workflow guide and help text now say to put trial permissions in the fixture's `.claude/settings.json`.
