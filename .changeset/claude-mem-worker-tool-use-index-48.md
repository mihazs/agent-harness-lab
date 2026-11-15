---
"agent-harness-lab": minor
---

Read the Claude-Mem worker's tool-use index as separate `tool_use` claims, opt-in. A `claude-mem-worker` selection with `"tool_uses": true` also reads `GET /api/tool-uses` for the one named project: identity and sizes only, never a tool input or response body, with the POST body route never requested. The option is absent from a selection declared before it existed, which keeps its scope digest; declaring it changes the selection digest, so an unchanged manifest scope refuses it. The index has no cursor, so a page shorter than the limit ends it, a full page is followed by another request, and a full page at `max_rows` is `pagination_incomplete`. A tool use shares the namespace and session fingerprints of the other claims, is scoped on `project` alone, never becomes an independent case, evidence of use or a task outcome, and the summary warns `tool_events_unverified`. Part of #48.
