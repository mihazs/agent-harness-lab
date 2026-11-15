---
"agent-harness-lab": minor
---

Add an offline `sources.ci_join.join_quality_evidence` that relates one exact commit's imported SARIF quality findings to the GitHub Actions jobs captured for the same head. It keeps findings apart from test results under its own profile, links a finding to a job and step only through a caller-declared report origin, and keeps reruns correlated, causal status unknown and task outcomes unverified (part of #53).
