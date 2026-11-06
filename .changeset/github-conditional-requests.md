---
"agent-harness-lab": minor
---

Add opt-in GitHub conditional requests: `GitHubAPI(..., conditional=True)` sends `If-None-Match` with validators held in memory from a prior successful `200`, and replays a `304 Not Modified` as the previously validated response charged its original bytes, so an unchanged recapture yields the same snapshot and never advances the checkpoint. Unsolicited or mismatched 304s reject the capture, failed captures keep the last successful validators, and validators never reach the journal or summary. The default sends no validators.
