---
"agent-harness-lab": patch
---

The recheck of suspect tasks no longer needs the two arms to state the same reason: any claim task both arms fail with a stated grader reason goes back to the evaluation author once, since graders quote each arm's own output in their messages. A recheck lasts exactly one attempt: when its rebuilt evaluation refutes the claim, the next attempt asks the proposer again instead of rebuilding the same candidate a second time.
