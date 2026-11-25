---
"agent-harness-lab": patch
---

In an installing run, a trial the agent did not finish inside the per-trial limit counts as a failed trial (marked `timed_out`) instead of ending the whole run as `evaluation_failed`; a loss or a fix that rests on it still has to hold in the fresh repeats.
