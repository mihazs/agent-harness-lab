---
"agent-harness-lab": patch
---

`ahl improve` and the `harness-self-improvement` skill now evaluate before they propose. When no task bank is pinned, a native run constructs an evaluation and scores the baseline first instead of falling back to the improve-first single pass, and the proposer receives the graded baseline outcomes. Claude Code trials request `acceptEdits` and the runtime's command sandbox: before, every trial ran in permission mode `default`, was denied each edit and command, and no candidate could score better than the baseline. A deferred mechanism is offered again on the next incremental run, proposal dispatches are bounded by `--max-rounds`, and a construction trace is no longer read back from disk.
