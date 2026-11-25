---
"agent-harness-lab": patch
---

Processes a trial or grader command leaves running, such as build daemons that detach into their own session, are killed when the command ends. They used to pile up over the trials of a run until the run was killed by the memory limit before reaching a verdict. On Linux with a systemd user manager, each agent session also runs in a memory-limited scope of its own (`AHL_SESSION_MEMORY_MAX`, default 8G, `off` to disable), so a session whose commands outgrow the limit is killed alone instead of taking the run down.
