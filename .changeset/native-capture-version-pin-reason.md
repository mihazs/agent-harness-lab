---
"agent-harness-lab": patch
---

Report a typed `capture_version_differs_from_pin` reason (observed and pinned versions, counts only) when an explicit native Claude or Codex selection meets a capture recorded by another runtime version, record `pin_compatibility` (`exact`, `declared_range`, `unverified`) per source, report selected, parsed and skipped capture counts with reasons, and accept `--session` for `ahl improve --history`. Closes #697.
