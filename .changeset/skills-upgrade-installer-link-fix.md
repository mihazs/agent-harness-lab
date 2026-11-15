---
"agent-harness-lab": patch
---

Fix `ahl skills` crashing with a raw `ValueError` traceback on an installer-managed layout. A link to `harness-self-improvement` that points into an older installer release is now treated as ours, like the stale `harness-evaluation` link, and is moved into the backup before the current link is created, so the two-skill to one-skill migration completes in one run. A link that points anywhere else, or a non-directory, is still refused, now as a single `ahl skills:` line that says nothing was changed instead of a traceback.
