---
"agent-harness-lab": minor
---

Each native evaluator trial now records `skill_shadowing`: a read-only look at the runtime's user-level skills folder for same-name copies of the staged skills (`ambient_same_name`, `none_found` or `unknown`, with bundle hashes and `bundle_match`, the effective layer always `unknown`). It reads only the staged names, never lists or writes the folder and never turns an unscanned layer into `none_found`. `paired_transfer` counts records flagged `ambient_same_name` and returns `inconclusive` with reason `shadowed_skill_copy` rather than certify a contrast that a shadowing copy could confound; unknown shadowing never filters or labels a record.
