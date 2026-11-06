---
"agent-harness-lab": minor
---

Report the owned Git harness snapshot binding inside the live controller source receipt. `SourceSink` accepts an optional `harness_capture`; each successful `finish` then adds `live_verification.harness_snapshot_binding`, the same receipt `harness_binding.bind` returns for that attempt, next to the verified session, attempt and grader revision. Unowned captures report `unknown`, incomplete attempts report no binding, and nothing new is journaled, imported or added as an identity field.
