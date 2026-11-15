---
"agent-harness-lab": minor
---

Record a caller-declared Codex managed folder as read-only influences in the declared Codex instruction inventory (`capture(..., codex_managed_root=...)`). The folder is never discovered, defaulted or read from the environment, must be separate from the checkout and `CODEX_HOME`, and link, hard-link and `.git` paths fail closed. `managed_config.toml` and `requirements.toml` each get a path-free `codex_managed` node that is hashed but never decoded or parsed and stays `unknown` (they may override the caller-declared profile); missing files stay `unknown`, an unreadable file blocks the receipt, and project nodes, edges and the byte budget are unchanged. Observed loading stays unknown and nothing grants edit authority.
