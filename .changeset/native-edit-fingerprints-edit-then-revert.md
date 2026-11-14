---
"agent-harness-lab": minor
---

Finish the last weak-outcome input of #666 (#766): pinned native Claude `Write` and `Edit` tool calls now carry content-free, case-salted edit fingerprints (`edit_target_fingerprint`, `edit_after_fingerprint` and, for an `Edit`, `edit_before_fingerprint`), so the `edit_then_revert` signal becomes computable on native histories instead of staying `unknown`. A write returning to an earlier content, or the reverse of an edit pair on the same target, is an `observed_pattern` with competing explanations (an edit pair covers a fragment, not the whole file). Only inputs that name a whole state are fingerprinted: a multi-edit, a patch, a file-creating edit, a shell command and every Codex call stay `unknown`. Paths and text are hashed and never stored; the fields stay out of duplicate checks and the body-insensitive metadata fingerprint, and at most 256 targets are tracked per case. See `docs/telemetry.md#edit-fingerprints`.
