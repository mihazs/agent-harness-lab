---
"agent-harness-lab": minor
---

Add `agent_harness_lab/surfaces.py`, which now owns harness-surface classification and the writable allowlist. `classify(relative_path, runtime, mode)` returns the kind, scope directory, executable flag, writability (`adoptable`, `contract_required`, `recommendation_only`, `private_excluded` or `unsupported`) and plan facets. Candidate scope checks, journal ownership, skill-bundle discovery, root instruction snapshots and change-plan operations now call it, and their verdicts are unchanged: no new path becomes writable. The module also holds a frozen `CAPABILITIES` table pinned to Claude Code 2.1.317 and Codex 0.166.0 (`openai/codex@d786ff91`), with public doc URLs. Items the 2026-11-05 research did not confirm stay `unverified`, and a row without a doc URL, the runtime pin or a status is rejected. A characterization table pins today's verdicts for every caller.
