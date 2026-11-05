---
"agent-harness-lab": patch
---

State the improve/watch recommendation boundary in CLI help and the README. Top-level, `ahl improve --help` and `ahl watch --help` now say what each command changes (private run artifacts, a run lock and, for watch, its checkpoint) and what AHL itself never writes (project instructions, skills, hooks and other harness files). The help also says the agent each command starts is cooperative: read-only project access is instructed, not enforced, and AHL rejects a stale result after a concurrent project change without reverting it. `--repair-contracts` is named as the only path that may apply a bounded skill-header repair, which a project `ahl.json` keeps recommendation-only. Watch help now says it reruns on changed evidence or execution configuration, not only on changed evidence. Command behavior and runtime gates are unchanged.
