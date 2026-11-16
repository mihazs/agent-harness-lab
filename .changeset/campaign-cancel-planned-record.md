---
"agent-harness-lab": minor
---

Add `ahl campaign --store STORE cancel --campaign ID`, an offline cancellation of a planned campaign that never dispatched anything, and the campaign lifecycle table `agent_harness_lab.campaigns.state` (`planned`, `ready`, `running`, `paused`, `blocked`, `interrupted`, `awaiting_review`, `budget_exhausted`, `completed`, `cancelled`, `failed`). Cancelling appends one `cancelled` event and retains the record and specification; a repeat returns the same record and writes nothing. `inspect` now reports the latest journal state and rejects an event bound to another specification, an unknown event kind, or a transition the table does not allow. The store still accepts only the `created` and `cancelled` events, so nothing is executed, reserved, reviewed or deployed. Documented in `docs/campaign-specification.md`.
