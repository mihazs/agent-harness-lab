---
"agent-harness-lab": minor
---

Judge a candidate against its baseline on identical frozen inputs and block a worse one. Every covered default `ahl improve` evaluation now carries a `before_after` block with both arm scores and intervals, the paired delta, and one frozen-inputs identity per arm (bank, grader, seed schedule, per-arm budget share and runtime hash) whose mismatch or unknown field is reported rather than read as a match. A candidate whose paired score falls below the margin ends `blocked` with reason `candidate_regressed` (exit 3, previously exit 1) instead of the bare `evaluation_failed`, and writes `investigation.json` with per-task and per-area deltas, the trials that flipped, whether the grader, frozen inputs or budget changed, and a `candidate_caused`, `pipeline_caused` or `undetermined` cause. A gain inside noise stays `inconclusive`, never `improved`, and an unknown outcome is never counted as a failure. The fixed repeated-bank lane is not wired yet.
