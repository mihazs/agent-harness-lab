---
"agent-harness-lab": minor
---

Add an opt-in `actions` detail to GitHub selection version 7 that reads workflow runs for each selected PR head and the jobs and steps of every attempt. Failed jobs and steps stay bound to the exact head and attempt, reruns stay correlated attempts rather than independent trials, unknown states reject the capture, and logs, artifacts and annotations are reported as not captured.
