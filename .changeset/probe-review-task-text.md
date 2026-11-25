---
"agent-harness-lab": minor
---

An installing `ahl improve` run now has a fresh, independent session read the task texts of each newly built evaluation before any trial, seeing only the claim and those texts. A task whose text states the rule, names the document that states it, or tells the agent to follow the project's conventions for exactly that behaviour is sent back to the author once to be rewritten as a plain user request; a review that cannot be had never stops the run, and the verdicts are recorded as `probe_review`.
