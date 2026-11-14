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
`sessions_captured`, `session_selected`, `pin_compatibility` counts (`declared_range` and `unverified`), `capture_reasons`, `cwd_drift` (records per working-directory class when native Claude captures changed directory; counts only), `runtime_lag` (only when a recorded release is unverified and not older than the first verified one, either newer than the newest or in a gap between verified ranges: counts per version and the exact next step), `events`, `cases`, `scope`, `since` and an `unknown` list.
Capture completeness and recurrence outside the captured sessions are always
listed as unknown; a repeated pattern is reported only when it was observed in
the captured sessions. The object holds counts only, no paths or message bodies.

## Try it in a worktree

Sessions are looked up by the project's own path, and a linked worktree has a
different path from the repository's main checkout. `ahl analyze` or `ahl improve`
of a worktree therefore finds no captures even when the repository has recorded
sessions. When the project is a linked worktree and no capture matched its path,
the receipt says so instead of finishing as an empty success: `ahl analyze` adds
`capture_reasons: [{reason: history_not_found_for_worktree, captures: 0}]`, and
`ahl improve . --history` stops with `status: blocked` and that `reason`.
The finding is "history not found for this path", never "nothing to learn".
The reason is given only when no `--since`, `--source`, `--session` or
`--max-capture-scan` limit applied: when one did, the cause of the empty result
is unknown, so the receipt keeps that request's own reason (for example
`history_no_matching_sessions`) and its next step.

To read the main checkout's sessions on purpose, pass it explicitly:

```sh
ahl analyze ../linked-worktree --history-from .
ahl improve ../linked-worktree --history --history-from .
```

`--history-from CHECKOUT` is opt-in and read-only. `CHECKOUT` must be another
checkout of the same repository (the same Git common directory, read from files
without running Git); a directory of an unrelated project is refused with exit
code 2, so the option cannot widen the scope to other projects. It excludes
`--events` and `--source-root`. The receipt carries
`capture_reasons: [{reason: source_project_path_differs, captures: N}]`; only
that checkout's sessions are read (the worktree's own are not merged in), and
findings describe its history. Evaluation reports are still read from the project path.
A repository whose Git files cannot be read stays unknown: no worktree claim is
made. The receipt holds counts only, no paths.

## When the history has findings but no verified outcome

A first run often finds recurring mechanisms in the history while no evaluation record
carries a verified outcome, so nothing in the traces can support a repair. `ahl improve --history`
then audits the project itself (the read-only, model-free static audit of the selected
`--agent` runtime) before it prepares a candidate:

- If a convention is eligible, the best one is staged as a hypothesis labelled
  `hypothesis_source: static_convention` with its `finding_id` and `convention_source`, and the
  usual paired baseline/candidate evaluation runs. The receipt carries `audit_hypothesis`,
  `static_convention_candidates` (the selection and the eligible conventions it left for a later
  run) and a `provenance` entry whose source is `static_audit`. It is a convention check, never a
  diagnosis; nothing is applied, and the promotion rules are unchanged.
- If none is eligible, the run ends with `status: no_change`,
  `reason: no_change_no_evaluable_candidate`, `causes` and `remediation`, and no model call. A
  `static_audit` cause that says `instruction_totals_unsettled` means the instruction chain could not be
  settled, so some conventions were not evaluated; it is not a finding that they are clean.
- Any run that returned a preparation model call and evaluated no trial (for example the proposer
  declines the staged convention) carries `spent_without_evaluation` with
  `provider_reported_spend: unverified`; the provider's own usage figure is not read. A run that evaluated
  trials, a paired evaluation or a fixed-bank comparison, never carries it.

A run with a verified outcome, `--focus` or `--from-audit` keeps its existing path.
See [static audit](static-audit.md#staging-static-conventions-on-a-first-run) for the eligible conventions.

## When the request cannot be satisfied

The command stops before any model spend with `status: blocked`, exit code 3, a
typed `reason` and the exact next step in `remediation`:

| `reason` | Meaning |
| --- | --- |
| `history_runtime_has_no_captures` | No captured events of the `--source codex` or `--source claude` runtime matched this project, `--since` and `--session`. |
| `history_source_ai_memory_unsupported` | `ahl improve` reads native sessions only and opens no network connection. Use `ahl analyze . --memory-manifest MANIFEST --authorization-file FILE` for ai-memory evidence. |
| `history_no_matching_sessions` | No captured events matched this project, `--since` and `--session`. |
| `history_not_found_for_worktree` | No session was recorded under this path and the project is a linked Git worktree, so the main checkout's history was not read. See [Try it in a worktree](#try-it-in-a-worktree). |
| `no_outcome_evidence` | Sessions were captured and show findings, but no verified outcome relates them to a failure and no `--focus` or `--from-audit` hypothesis was supplied. No model call was made; `findings_not_candidates` counts why no finding became a candidate. |

When you see `no_outcome_evidence`, add verified evidence (an executable-grader evaluation under `artifacts/`),
run `ahl audit .`, or pass `--focus` with a hypothesis to test; rerunning unchanged repeats the same result at no cost.
A run that did call the model and evaluated nothing is labelled `spent_without_evaluation`.

Malformed requests exit 2 before any run starts: `--since` or `--source` without
`--history`, an invalid `--since` or `--session`, or `--history` together with `--events` or
`--source-root`.

The full reason table is in [analysis](analysis.md#exit-reasons-and-next-actions).
Run directories honor `AHL_STATE_HOME` (the state root itself, for example in
tests); otherwise `$XDG_STATE_HOME/agent-harness-lab` is used.
