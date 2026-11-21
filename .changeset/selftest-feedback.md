---
"agent-harness-lab": patch
---

An installing `ahl improve` run now sends a grader self-test failure (and other defects its own graders show) back to the evaluation author as a repair pass, naming the task, the control and the grader's bounded output, instead of ending `invalid_input`.
