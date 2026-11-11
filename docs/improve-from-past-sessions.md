# Improve from past sessions

Use this when you want `ahl improve` (or the `harness-self-improvement` skill,
through `ahl skills invoke`) to evaluate and improve from sessions you already
recorded, instead of only the current harness.

```sh
ahl improve . --history                  # every captured session for this project
ahl improve . --history --since 2025-01-31
ahl skills invoke . --history --since first
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
- `--source native` is the only supported source (also the default).

## Read the result

The receipt and the private `analysis.json` carry a `history` object with
`sessions_captured`, `events`, `cases`, `scope`, `since` and an `unknown` list.
Capture completeness and recurrence outside the captured sessions are always
listed as unknown; a repeated pattern is reported only when it was observed in
the captured sessions. The object holds counts only, no paths or message bodies.

## When the request cannot be satisfied

The command stops before any model spend with `status: blocked`, exit code 3, a
typed `reason` and the exact next step in `remediation`:

| `reason` | Meaning |
| --- | --- |
| `history_source_ai_memory_unsupported` | `ahl improve` reads native sessions only. Use `ahl analyze . --memory-manifest MANIFEST --authorization-file FILE` for ai-memory evidence. |
| `history_no_matching_sessions` | No captured events matched this project and `--since`. |

Malformed requests exit 2 before any run starts: `--since` or `--source` without
`--history`, an invalid `--since`, or `--history` together with `--events` or
`--source-root`.

The full reason table is in [analysis](analysis.md#exit-reasons-and-next-actions).
Run directories honor `AHL_STATE_HOME` (the state root itself, for example in
tests); otherwise `$XDG_STATE_HOME/agent-harness-lab` is used.
