---
"agent-harness-lab": patch
---

Close the gap in the declared Claude Code compatibility range: add the `2.1.317` synthetic contract fixture (field names of a real 2.1.317 capture, invented values only) and widen `VERIFIED_RANGES` to the contiguous `2.1.316` to `2.1.318`. Sessions recorded by 2.1.317 are now read by the verified adapter and counted as `pin_compatibility: declared_range`, instead of falling back to the descriptive, unverified adapter with `capture_version_differs_from_pin`. The identity, pull-request link, permission-mode and agent-name records that 2.1.317 captures carry are counted per type as `unknown_entry_type`, never parsed. Releases outside the range stay unverified.
