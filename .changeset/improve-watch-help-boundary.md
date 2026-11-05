---
"agent-harness-lab": patch
---

State the improve/watch recommendation boundary in CLI help and the README. Top-level, `ahl improve --help` and `ahl watch --help` now say what each command changes (private run artifacts, a run lock and, for watch, its checkpoint) and what it never changes (project instructions, skills, hooks and other harness files). `--repair-contracts` is named as the only path that may apply a bounded skill-header repair, which a project `ahl.json` keeps recommendation-only. Watch help now says it reruns on changed evidence or execution configuration, not only on changed evidence. Command behavior and runtime gates are unchanged.
