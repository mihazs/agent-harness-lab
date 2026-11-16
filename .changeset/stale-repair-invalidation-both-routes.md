---
"agent-harness-lab": patch
---

Pin the stale-plan checks of the deterministic skill-header repair through both entry routes. A user edit, an added harness file or a swapped candidate after staging, and an edit after the journal is prepared or at the last check before the replace, now each have a test on `ahl improve` and on `ahl skills invoke`: the repair stops with the user's bytes, an unrelated file and the project's other harness files intact, no deployment is committed, both routes print the same typed receipt and message (`invalid_input`, or `requires_intervention` for the edit caught inside the journal transaction), and a fresh run applies once and rolls back to the user's bytes. The checks and their outcomes are documented in the format-repair section of the one-call guide. No runtime behavior changes. Part of #143.
