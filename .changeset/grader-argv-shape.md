---
"agent-harness-lab": patch
---

The evaluation author's contract now shows the grader argv shape (an interpreter first, such as `["python3", "graders/check.py", "{workspace}"]`), and a grader self-test repair asks the author to run every grader on its controls, not only the one reported, so a defect shared by several graders no longer exhausts the repair passes.
