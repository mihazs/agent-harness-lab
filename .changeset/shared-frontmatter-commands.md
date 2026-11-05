---
"agent-harness-lab": patch
---

The command, skill and prompt inventory now reads frontmatter only through `texts()`, a new text mode of the shared bounded reader in `sources/frontmatter.py`, and keeps no frontmatter logic of its own. `texts()` drops a leading BOM, returns `None` without a leading fence, `''` for an empty value and `None` for a list or mapping, and reports a space-padded fence as `unsupported_yaml`. `read()` and `string_lists()` are unchanged, and inventory verdicts are the same as before.
