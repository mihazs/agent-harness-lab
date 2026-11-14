---
"agent-harness-lab": minor
---

Add opt-in read-only Mem0 OSS REST and hosted Platform v3 source clients (`mem0-oss-rest`, `mem0-platform-api`) with an explicit HTTPS origin, a named credential file or variable, scoped filters that refuse wildcards, bounded pagination and 401/403/404/429 coverage stops. They reuse the offline Mem0 claim mapping, never call a write route, and never turn a memory into a verified task outcome. Part of #49.
