---
"agent-harness-lab": minor
---

Add explicit offline fixture-command execution to paired evaluations. Executors
receive only their fresh fixture and selected read-only harness; a separate
offline grader evaluates bounded artifact copies with a pristine oracle bundle.
Unsupported sandbox hosts block without host fallback. Export artifact allowlists
with task-bank manifests. This mode makes no model calls or automatic promotions.
