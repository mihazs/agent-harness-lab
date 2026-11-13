---
"agent-harness-lab": minor
---

Add `agent_harness_lab.memory.reuse`, a pure offline stage reuse planner: canonical domain-separated node keys, a cycle-checked stage dependency graph with transitive invalidation, typed hit/miss/invalid/blocked reason codes, and a frozen `ahl-stage-reuse-v1` receipt that lists reused, executed, excluded and unknown stages. Unverified freshness or an unchecked live authority is reported as unknown, never as a hit. Committing outputs to the store and a CLI are left to later slices.
