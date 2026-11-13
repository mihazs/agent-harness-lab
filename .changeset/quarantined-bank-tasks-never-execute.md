---
"agent-harness-lab": patch
---

Part of #8: a task declared `review_status: quarantined` in a task bank no longer enters an executable manifest or a grader audit. `ahl bank` now lists such tasks in `quarantined_tasks` with a `task_quarantined` issue, `--export` leaves them out and reports `quarantined_excluded`, `--audit` skips their controls and reports `quarantined_skipped` (so a quarantined broken grader no longer blocks the audit of the rest), an explicit export selection naming one is refused, and a bank with no executable task is refused instead of exporting or auditing nothing. Quarantined tasks still count for leakage, content and root checks, and no certification or independence claim changes.
