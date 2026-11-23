---
"agent-harness-lab": patch
---

When a Claude subscription login exists, a gateway address (`ANTHROPIC_BASE_URL`) and its model aliases are dropped together with the API key or token, instead of sending the login to a gateway that refuses it.
