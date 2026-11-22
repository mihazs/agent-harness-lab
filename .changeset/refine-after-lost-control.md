---
"agent-harness-lab": patch
---

In an installing run, a first candidate rejected by structural validation no longer uses up the refinement attempt, and a candidate that fixes the reproduced weakness but breaks a control is refined on the same frozen tasks instead of ending the run. An authored `grader_bundles` written as an empty list or null is read as no bundles instead of costing a repair pass. Held-out tasks of one evaluation may share a fixture; only held-out inputs an earlier evaluation used are rejected as stale.
