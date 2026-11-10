# Analyze an explicit ai-memory session

[Workflows](workflows.md) · [Source contracts](source-contracts.md)

`ahl analyze . --memory-manifest ./memory-session.json --authorization-file ./memory-authorization`
reads one explicitly selected session over stateless HTTPS MCP. It does not call
an inference model or enumerate other sessions/scopes. The authorization file
contains the complete authorization header value and is never discovered automatically.

Use this manifest shape with your verified server version and schema fingerprint:

```json
{
  "schema_version": 1,
  "endpoint": "https://memory.example.invalid/mcp",
  "workspace": "synthetic-workspace",
  "project": "synthetic-project",
  "session_id": "00000000-0000-4000-8000-000000000001",
  "server_version": "2.4.0",
  "tool_schema_sha256": "REPLACE_WITH_VERIFIED_SCHEMA_SHA256"
}
```

The session must be its canonical UUID. Pin the `inputSchema` returned by the
trusted server's `tools/list` for `memory_read_session_observations`: the fingerprint
is SHA-256 of UTF-8 JSON with sorted keys, compact separators and no nonfinite values.
No credentials belong in the manifest. An optional `limits` object accepts positive
`timeout`, `seconds`, `max_bytes`, `max_pages` and `max_requests`; without explicit
limits the client reads every observation page. `max_pages` also constrains MCP
tool discovery; `max_requests` includes initialization, discovery and readback.

The reader normalizes lifecycle activity and checks every page again before
acceptance. Changed pages, duplicates, unsupported pins and missing authorization
fail explicitly without retries. Readback does **not** prove an atomic server
snapshot. `memory_source` in the JSON receipt and private analysis reports these
coverage limitations and rows elided from other scopes. Only observation ID, session,
kind and creation time are normalized. The upstream observation record has no
structured tool name, call ID or result status, so those stay unknown; titles,
bodies, importance and extension event names are never parsed for them (see
[observation field contract](source-contracts.md#session-observation-field-contract)).
These observations cannot certify a no-defect outcome
or grant permission to apply a candidate. The receipt marks the analysis
`incomplete` with a caveat naming the missing fields as source insufficiency, not a
no-defect outcome, and `next_action` no longer suggests `ahl improve`. Use an
explicitly supported native capture when structured diagnostic metadata is needed.
CLI progress reports aggregate page/observation counts on stderr during retrieval
and readback; stdout remains the JSON receipt and library calls are silent by default.
