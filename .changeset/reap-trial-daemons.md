---
"agent-harness-lab": patch
---

Processes a trial or grader command leaves running, such as build daemons that detach into their own session, are killed when the command ends. They used to pile up over the trials of a run until the run was killed by the memory limit before reaching a verdict.
