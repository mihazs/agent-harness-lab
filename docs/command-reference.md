# Command overview

[Workflows](workflows.md) · [CLI help](cli-help.md)

| Command | Purpose |
| --- | --- |
| `ahl improve [project]` | Discover issues, stage repairs and evaluate; behavioral adoption is blocked |
| `ahl analyze\|improve [project] --history-from CHECKOUT` | Read the sessions recorded for another checkout of the same repository, read-only and labelled `source_project_path_differs`, when the project is a linked worktree; see [improve from past sessions](improve-from-past-sessions.md#try-it-in-a-worktree) |
| `ahl improve [project] --history [--since DATE\|first]` | Use captured past project sessions as read-only evidence; see [improve from past sessions](improve-from-past-sessions.md) |
| `ahl improve [project] --repair-contracts --scope project\|global\|project+global [--agent codex\|claude]` | Repair skill headers in the project (default), the named runtime's user-level skill directory, or both; see [choose the project or user-level scope](improve-scope.md) |
| `ahl watch [project]` | Rerun recommendation-only evaluation when evidence or configuration changes; AHL never writes harness files |
| `ahl analyze [project]` | Automatically collect and analyze evidence without an agent |
| `ahl diagnose --events FILE --output DIR` | Diagnose an explicit event file |
| `ahl mine FILE` | Mine event transitions and recurring root-case support |
| `ahl evaluate --manifest FILE --baseline DIR --candidate DIR --output DIR` | Execute a prepared baseline/candidate experiment |
| `ahl benchmark FILE --method baseline` | Compare incident-classifier predictions (`--reject-leakage` fails on [shared projects or texts](validation.md#benchmark-leakage-disclosure)); optional [frozen triage policy](diagnostic-triage.md) or `--profile diagnostic-corpus` for a [corpus development export](diagnostic-corpus.md#benchmark-input-profile) |
| `ahl apply RUN [--project DIR] [--authorize-plan SHA256]` | Review the saved change plan of an improvement run (read-only); with the exact plan hash, apply only an independently reproduced skill-header repair ([details](one-call-improvement.md#review-and-apply-a-saved-plan)) |
| `ahl compare --experiment FILE --output DIR` | Render a private baseline/candidate experiment record as a static offline HTML review page ([details](side-by-side-reports.md#static-viewer-ahl-compare)) |
| `ahl memory init\|status\|verify\|reindex\|gc --store DIR` | Create and maintain an opt-in private evidence store; offline, see [private memory store](memory-store.md) |
| `ahl memory history --history-store DIR` | Read-only list of existing evaluation histories by reference with live authority checks; see [history index](memory-store.md#list-existing-evaluation-histories) |
| `ahl sources claude pin-check --events FILE` | Compare one Claude Code capture with the verified profile contract by field name only; read-only, see [verify a new Claude Code release](verify-claude-release.md) |
| `ahl sources claude-mem tool-check --tools FILE` | Compare one saved Claude-Mem MCP `tools/list` result with the pinned v15.9.0 read tools by input-schema structure; offline, no worker contact, see [Claude-Mem tool listing check](source-contracts.md#saved-mcp-tool-listing-check-claude-mem) |
| `ahl sources mempalace capabilities [--tools-list FILE]` | Print the offline MemPalace profile and tool capability report, optionally comparing a saved `tools/list` result by name only; no server, network or model, see [MemPalace capability report](source-contracts.md#mempalace-capability-report) |
| `ahl doctor [project] --profile offline --json` | Check readiness without running an agent; checks and boundaries are in [offline readiness](onboarding.md). Its non-required `claude_runtime_lag` warning reads dotted version strings from at most 200 Claude session transcript headers, under every profile |
| `ahl skills --agent claude` | Link installed skills into a selected runtime's skill directory |

Run `ahl COMMAND --help` for the complete options. Low-level evaluation supports
`--agent claude` or `--agent codex`; preparation and evaluation never mix runtimes
within a pair. CLI executables can be selected with `--claude` or `--codex`.

The installed skills also work inside an existing agent session. They discover
improvement candidates themselves; an incident description or proposed candidate
is optional. In Claude Code their commands are `/harness-self-improvement` and
`/harness-evaluation`. During a CLI-managed run, the controller owns execution and
adoption; the skills must not launch recursive improvement controllers.
