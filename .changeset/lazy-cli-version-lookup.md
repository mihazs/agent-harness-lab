---
"agent-harness-lab": patch
---

Start every command about 8 ms faster by resolving the package version, and importing importlib.metadata, only when --version is requested.
