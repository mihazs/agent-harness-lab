# Improve from past sessions

Use this when you want `ahl improve` (or the `harness-self-improvement` skill,
through `ahl skills invoke`) to evaluate and improve from sessions you already
recorded, instead of only the current harness.

```sh
ahl improve . --history                  # every captured session for this project
ahl improve . --history --since 2025-01-31
ahl skills invoke . --history --since first
ahl improve . --history --source claude        # only Claude sessions
ahl improve . --history --session SESSION_ID   # one exact session only
```

Both routes take the same options and return the same receipt shape and exit codes.

## What the request does

- It reuses the existing collection of native Codex and Claude sessions
  (`CODEX_HOME`, `CLAUDE_CONFIG_DIR`) that `ahl analyze` uses. No adapter is added.
- It is read-only toward the session stores: files are opened for reading and
  never modified, moved or deleted.
- It is scoped to the project: a session whose recorded working directory is not
  the project is not captured.
- `--since first` (the default with `--history`) places no lower bound, so the
  earliest available session is included. `--since DATE` keeps capture files
  modified on or after an ISO 8601 date (UTC when it has no offset). It filters by
  file modification time, not by the time of each event.
- `--session ID` keeps only the exact session UUID: a Claude session (its main and subagent
  transcripts) or a Codex thread; it combines with `--since`. A session recorded
  by a Claude Code release inside the fixture-verified range is read by the verified adapter
  and counts as `pin_compatibility: declared_range`; a session outside it is still read
  descriptively, is marked `unverified` and adds the
  `capture_version_differs_from_pin` caveat and `capture_reasons` entry to the
  receipt (observed and verified versions only). A session that matches nothing
  stops with `history_no_matching_sessions`.
- `--source native` (the default) reads both runtimes; `--source codex` or
  `--source claude` narrows the same discovery to that runtime's sessions. When the
  narrowed runtime has no matching captures the command stops with
  `history_runtime_has_no_captures`, never falling back to the other runtime.
- `--source ai-memory` is not supported here: `improve` never opens a network
  connection. ai-memory evidence stays with `ahl analyze` and its explicit
  manifest and authorization file.

## Read the result

The receipt and the private `analysis.json` carry a `history` object with
`sessions_captured`, `session_selected`, `pin_compatibility` counts (`declared_range` and `unverified`), `capture_reasons`, `runtime_lag` (only when a recorded release is newer than the newest verified one: counts per version and the exact next step), `events`, `cases`, `scope`, `since` and an `unknown` list.
Capture completeness and recurrence outside the captured sessions are always
listed as unknown; a repeated pattern is reported only when it was observed in
the captured sessions. The object holds counts only, no paths or message bodies.

## When the request cannot be satisfied

The command stops before any model spend with `status: blocked`, exit code 3, a
typed `reason` and the exact next step in `remediation`:

| `reason` | Meaning |
| --- | --- |
| `history_runtime_has_no_captures` | No captured events of the `--source codex` or `--source claude` runtime matched this project, `--since` and `--session`. |
| `history_source_ai_memory_unsupported` | `ahl improve` reads native sessions only and opens no network connection. Use `ahl analyze . --memory-manifest MANIFEST --authorization-file FILE` for ai-memory evidence. |
| `history_no_matching_sessions` | No captured events matched this project, `--since` and `--session`. |

Malformed requests exit 2 before any run starts: `--since` or `--source` without
`--history`, an invalid `--since` or `--session`, or `--history` together with `--events` or
`--source-root`.

The full reason table is in [analysis](analysis.md#exit-reasons-and-next-actions).
Run directories honor `AHL_STATE_HOME` (the state root itself, for example in
tests); otherwise `$XDG_STATE_HOME/agent-harness-lab` is used.
