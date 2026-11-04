---
"agent-harness-lab": patch
---

Bound the Codex `0.166.0-v2` root walk to 128 directory levels and 10,000 visited entries with an iterative traversal. Reaching a bound now reports a labelled `scan_depth`/`scan_entries` limit with an unknown `walk_limits` receipt and blocks the checkpoint instead of walking unbounded trees or crashing with a recursion error. In-bound scans are unchanged; the Claude subagent walk shares the same helper.
