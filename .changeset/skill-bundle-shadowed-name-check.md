---
"agent-harness-lab": minor
---

Staged skill bundle review blocks a newly created bundle that shadows, or is shadowed by, a same-named bundle in the same kind of skills folder (`.claude/skills`, `.agents/skills` or a bare `skills`) at the same, an enclosing or a nested directory, with the fixed reason `shadowed_name`. Updates, removals, sibling packages, cross-kind copies and scoped snapshots are not compared; this is static and claims no runtime activation. Part of #78.
