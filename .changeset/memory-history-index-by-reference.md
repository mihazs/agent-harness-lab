---
"agent-harness-lab": minor
---

`ahl memory history --history-store DIR` (repeatable) lists the runs of explicitly selected evaluation HistoryStores as read-only JSON rows that reference the original store (path-free `authority_store_ref` plus `original_run_id`) with spec, bank, oracle, harness and runtime digests. Each read re-runs the HistoryStore's own validators, so an oracle quarantine or a bank revocation changes the next listing and an old result stays viewable as `invalidated_by_oracle_quarantine` or `authority_not_current`, never as current proof. Runs missing bank binding, snapshot, grader or runtime provenance are `legacy_unverified` with the field left null, and unreadable runs are listed as `unknown`. Nothing is written to or copied from any store, and isolated sandbox roles are denied. Part of #629. Closes #634.
