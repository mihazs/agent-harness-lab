---
"agent-harness-lab": patch
---

An installing `ahl improve` run now sends a grader self-test failure (and other defects its own graders show) back to the evaluation author as a repair pass, naming the task, the control and the grader's bounded output, instead of ending `invalid_input`. An installing run builds its refinement evaluation afresh instead of reusing frozen tasks, so a control the baseline fails is not carried over and the refinement gets the same repairs. In a trial whose fixture is a git repository, the harness files AHL stages (instructions, skills, the trial plugin) are kept out of `git status`, so a grader that checks for a clean tree no longer fails both arms.
