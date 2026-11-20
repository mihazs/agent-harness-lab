---
"agent-harness-lab": patch
---

Claude Code trials can now change their workspace. A cooperative trial ran in permission mode `default` with project-only settings, so every edit and command was denied, the baseline could not pass a task that needs a change, and no candidate could score better; trials now request `acceptEdits` and the runtime's command sandbox with no unsandboxed fallback, the counterpart of Codex's `workspace-write`. The grader self-test now grades as a trial does: an exit-code grader declared with `grader_bundles` is rejected before any trial instead of turning every trial into `grader_error`, and the construction prompt states the two grader result forms.
