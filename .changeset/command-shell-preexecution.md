---
"agent-harness-lab": patch
---

Route Claude commands and `SKILL.md` files whose content holds shell pre-execution syntax (`` !`command` `` or a ` ```! ` block) to the hook contract instead of adopting them. Variants that the pinned Claude Code docs do not confirm, and inline forms inside plain fenced code blocks, fail closed.
