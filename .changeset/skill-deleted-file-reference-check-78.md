---
"agent-harness-lab": minor
---

Block a skill bundle update that deletes a file while a file left in the candidate state still names it (part of #78). Staged skill bundle review now reports `removed_file_referenced` when a remaining file, including the bundle's own `SKILL.md` and assets, another bundle or an instruction file such as `AGENTS.md`, links to a deleted file or names its path as a whole path (project-relative or relative to that file), under the same rules as `removed_bundle_referenced`; a reference the same candidate drops, a longer file name, a path in another folder and a scoped snapshot are not judged. The review is static, reads the staged bytes only, and claims no activation, utility or authorization.
