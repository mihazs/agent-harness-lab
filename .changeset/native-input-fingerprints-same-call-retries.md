---
"agent-harness-lab": minor
---

Finish the weak-outcome input slice of #666: pinned native Claude and Codex captures now add a content-free, case-salted `input_fingerprint` to tool calls (the Claude `tool_use` input, and the Codex `function_call` arguments, `custom_tool_call` input or `local_shell_call` action), so the `same_call_retries` weak-outcome signal and the `same_call_retry` next-action count become computable on real native histories instead of staying `unknown`. The input is hashed and never stored; the fingerprint compares only within one case and one tool, stays out of the body-insensitive metadata fingerprint and the duplicate-line check, and is absent when a call has no usable input. Edit-then-revert still needs state revisions that native captures do not record and stays `unknown` there (tracked in #766). See the comparison and privacy notes in `docs/telemetry.md`.
