---
"agent-harness-lab": patch
---

Fix `agent_harness_lab.optimization.router.run_queue` evaluating `tool_adapter` mechanisms without adapter authorization. The queue now checks each record's component through `route()`, sharing the held dispositions used by `dispositions`. An unauthorized adapter record is deferred as `adapter_route_not_authorized`: its evaluation callback is not invoked and no budget is charged. Pass `authorized_adapters=True` to evaluate adapter routes. Queues without adapter routes keep the same order, deduplication, budget charges and outcomes.
