---
"agent-harness-lab": minor
---

Add an opt-in user-level remembered runtime and runtime-narrowed history evidence. `ahl improve` / `ahl skills invoke --agent codex|claude --remember-runtime` stores the choice atomically with owner-only permissions in a versioned, bounded user-level file (never a project file); `ahl config show|clear` inspects or deletes it. The runtime policy now reads explicit `--agent`, then project `ahl.json`, then the remembered choice, then a single installed runtime, then the invoking host marker, then asks once; a corrupt or unsafe file is ignored. `--history --source codex|claude` narrows discovery to that runtime and stops with the typed `history_runtime_has_no_captures` reason when it has none; `--source ai-memory` stays unsupported with a no-network next step.
