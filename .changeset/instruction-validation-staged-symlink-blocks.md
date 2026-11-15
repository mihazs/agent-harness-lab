---
"agent-harness-lab": patch
---

Block a scoped instruction validation whose staged baseline or candidate tree holds a symlink instead of raising (part of #79). `instruction_validation.validate` documents that it never raises for a candidate defect, but a symlink in either staged tree escaped as a `ValueError`; it now returns a `blocked` validation with the reason `symlink_in_staged_tree` and no scope, and `current` reports a symlink that appears in the evaluated candidate as `candidate_changed`. The workflow already rejected a symlinked candidate earlier, so an `ahl improve` run behaves as before.
