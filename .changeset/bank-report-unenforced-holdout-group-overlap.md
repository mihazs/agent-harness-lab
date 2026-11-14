---
"agent-harness-lab": minor
---

Report holdout group overlap in task-bank validation and inspection (part of #8): when a bank does not list `family`, `project_group` or `time_group` in `separate_by`, a certification or transfer task that shares one of those values with a construction, development or calibration task is now reported as an `independence_unverified` issue with reason `group_overlap_not_enforced`, naming the dimension, the partition pair and the task IDs. Previously the overlap passed silently. Overlap on a listed dimension is still rejected, root leakage is always rejected, and no overlap is treated as verified independence. Banks with distinct groups, design-only banks and the bundled starter and adversarial banks are unchanged.
