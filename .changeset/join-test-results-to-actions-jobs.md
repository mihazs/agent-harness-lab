---
"agent-harness-lab": minor
---

Add an offline `sources.ci_join.join_ci_evidence` that relates one exact commit's imported JUnit/pytest results to the GitHub Actions jobs captured for the same head. It reports failed jobs with their failing step numbers and failed cases, links a case to a job and step only through a caller-declared report origin, and keeps reruns correlated, causal status unknown and task outcomes unverified (part of #53).
