---
"agent-harness-lab": minor
---

Add a shared forge rate-limit limiter: parse limiter headers and classify primary, secondary and permission 403s, stop at a reserve floor or per-run budget as blocked access with the reset time, share quota state across processes in the private state root, report a quota receipt, add read-only `ahl sources github quota`, and use it from `scripts/sync_project.py` (part of #665).
