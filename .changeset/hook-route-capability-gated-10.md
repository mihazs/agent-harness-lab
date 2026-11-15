---
"agent-harness-lab": minor
---

Add a capability-gated `hook` route to mechanism routing (part of #10). `hook` is a new responsible component, distinct from the presentation-only `tool_adapter`: a reviewed `hook` record is held as `deferred` with `hook_route_not_authorized` unless the caller passes `authorized_hooks=True` to `route`, `run_queue` or `dispositions`, and an authorized one still needs a successful control (`hook_route_requires_successful_control`). Both holds run before the evaluation callback and charge no budget, a proposer's `component: "hook"` claim is admitted only as an unreviewed `proposed_component`, and the coverage report counts `hook` among the responsible components. Not yet done: the native hook change itself (#80), composed-repair evaluation, append-only prediction revisions and live utility.
