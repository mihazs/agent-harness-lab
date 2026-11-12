---
"agent-harness-lab": patch
---

Fix #726: authored grader-bundle and control digests are now dropped at the same early point on every construction path, including the controlled and evaluation-store paths where they used to cost a repair pass, so no later reader of the author output sees them. A host-run grader is started with bytecode writing disabled and checked right after its self-test, so a grader that imports a sibling module (or writes beside its inputs) can no longer change its frozen bundle and end the run with the bare integrity sentence; a frozen input found changed is the typed `grader_input_changed_after_freeze` naming the stage and field. Every terminal receipt, including blocked and invalid ones, records `authored_digest_ignored` (a count) and `evaluation_construction_seconds`. Bank and replay validation still reject a wrong declared digest, and a grader or fixture byte changed after freezing still invalidates the run.
