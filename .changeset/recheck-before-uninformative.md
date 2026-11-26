---
"agent-harness-lab": patch
---

An installing run whose agents finished their turn but failed every task in both arms, controls included, with a stated grader reason now has the evaluation author rebuild the tasks (at most twice) before it ends `evaluation_uninformative`; a runtime failure still stops at once.
