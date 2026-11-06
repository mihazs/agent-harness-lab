---
"agent-harness-lab": patch
---

Make the hook-validation tests for lingering processes independent of runner speed. The background child now holds a FIFO gate open and blocks on it until the test releases it, so the test checks at the post-run snapshot and after the trial whether any process the run left behind is still alive, instead of racing a one-second sleep. The containment behaviour is unchanged.
