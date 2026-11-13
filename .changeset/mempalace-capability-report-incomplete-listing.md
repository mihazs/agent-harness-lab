---
"agent-harness-lab": patch
---

Make the MemPalace capability check treat a saved `tools/list` page with a `nextCursor` as incomplete: it adds `listing_incomplete` and reports `inconclusive`, not `incompatible`, when a profile tool is absent. Part of #50.
