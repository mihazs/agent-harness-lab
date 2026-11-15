---
"agent-harness-lab": minor
---

Block the complete removal of a project skill bundle while a file that remains in the candidate still names it (part of #78). A root `AGENTS.md` or `CLAUDE.md`, or any file of another bundle, that links into the removed bundle or mentions its folder by whole path (project-relative, or relative to that file's folder) now gives the removal the reason `removed_bundle_referenced`, so `ahl improve` stops with `skill_bundle_validation_failed` and `workflow.adopt` refuses it. A reference the same candidate also deletes does not count, so a consolidation passes once the surviving files stop naming the removed bundle. Bare skill names in prose, files outside the snapshot and scoped snapshots are not checked, and the reason names no file. No activation, utility or authorization claim changes, and behavioral candidates stay inconclusive.
