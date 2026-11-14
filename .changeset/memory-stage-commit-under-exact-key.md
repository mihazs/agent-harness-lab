---
"agent-harness-lab": minor
---

The memory store can now hold the outputs the stage reuse planner decides on. `agent_harness_lab.memory.commit` publishes a deterministic stage's output and one record under its exact node key (idempotent; another output under a committed key is refused), keeps a typed diagnostic event for a failed or interrupted stage that is never returned as reusable, and stores a frozen `ahl-stage-reuse-v1` receipt only when it lists every stage and keeps `reused_live_trials: 0` and `publication_allowed: false`. Committers of one project serialize on a bounded per-project lock (256 lock files, busy timeout from the store). Lookup re-verifies the record against its hash and key on every read, and records survive `reindex`. Library only; the CLI entry and the latest-record loader for the planner remain. Part of #633.
