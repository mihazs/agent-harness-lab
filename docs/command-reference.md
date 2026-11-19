# Command overview

[Workflows](workflows.md) · [CLI help](cli-help.md)

| Command | Purpose |
| --- | --- |
| `ahl improve [project]` | Run the evaluate, improve, evaluate loop on the pinned bank, or make the labelled single pass when none is pinned; behavioral adoption is blocked |
| `ahl analyze\|improve [project] --history-from CHECKOUT` | Read the sessions recorded for another checkout of the same repository, read-only and labelled `source_project_path_differs`, when the project is a linked worktree; see [improve from past sessions](improve-from-past-sessions.md#try-it-in-a-worktree) |
| `ahl improve [project] --accept-score X --max-rounds N [--plateau-rounds N] [--on-regression stop\|rollback_continue]` | Declare the bar and stop policy of the loop, which is the default on a pinned bank; one typed stop criterion and exit code per run, recommendation only and never certification; see [improve until an acceptance score is reached](improve-until-score.md) and the [loop reference](loop-reference.md#running-the-loop-from-ahl-improve) |
| `ahl improve [project] --single-pass` | Deprecated, removed after one release: the previous improve-first single pass; the receipt says `loop.status: not_used`; see [the default and `--single-pass`](loop-reference.md#the-default-and---single-pass) |
| `ahl improve [project] --area ID [--area ID ...]` | Aim the run at declared capability areas of a pinned bank and block a candidate that regresses a scored area: the default loop stops with `loop_regression`, and `--single-pass` ends with `area_regressed`; see [gate adoption per area](gate-adoption-per-area.md) |
| `ahl improve [project] --correction-excerpts` | Off by default: give the proposer model session bounded, scrubbed excerpts of your own recorded user messages from this project (`user-corrections.json`); see [instruction adherence](instruction-adherence.md) |
| `ahl improve [project] --history [--since DATE\|first]` | Use captured past project sessions as read-only evidence; see [improve from past sessions](improve-from-past-sessions.md) |
| `ahl improve [project] --repair-contracts --scope project\|global\|project+global [--agent codex\|claude]` | Repair skill headers in the project (default), the named runtime's user-level skill directory, or both; see [choose the project or user-level scope](improve-scope.md) |
| `ahl config show\|clear\|set-scope SCOPE --agent codex\|claude\|clear-scope` | Show or change the user-level remembered runtime and the saved default `--scope` (`global` or `project+global`); never a project file, see [save a default scope](improve-scope.md#save-a-default-scope) |
| `ahl watch [project]` | Rerun recommendation-only evaluation when evidence or configuration changes; AHL never writes harness files |
| `ahl analyze [project]` | Automatically collect and analyze evidence without an agent |
| `ahl analyze [project] --events FILE --memory-store DIR --incremental [--explain-reuse]` | Reuse the verified process-mining output of an unchanged capture and harness from an opt-in private store, and explain every hit and miss; offline, no model call; see [reuse analysis work on unchanged input](incremental-analysis.md) |
| `ahl improve [project] --events FILE --memory-store DIR --incremental [--explain-reuse]` | Reuse the verified process-mining output of an unchanged capture and harness in an improvement run and report it as the `evidence_collection` stage; trials and proposals are never reused; see [incremental runs](incremental-runs.md#reuse-the-evidence-stage) |
| `ahl diagnose --events FILE --output DIR` | Diagnose an explicit event file |
| `ahl instruction-adherence SESSIONS [--native claude\|codex --project DIR] [--rules LAYER=FILE]` | Report where captured sessions ignored harness rules or user-prompt instructions; offline, redacted by default, see [find where agents ignored instructions](detect-ignored-instructions.md) |
| `ahl instruction-repair plan REPORT` / `judge --target REF ...` | List candidate mechanisms and repair classes for ignored instructions, or screen one repair against recorded arms; offline, recommendation only, see [repair a harness whose instructions are ignored](repair-ignored-instructions.md) |
| `ahl mine FILE` | Mine event transitions and recurring root-case support |
| `ahl evaluate --manifest FILE --baseline DIR --candidate DIR --output DIR` | Execute a prepared baseline/candidate experiment |
| `ahl benchmark FILE --method baseline` | Compare incident-classifier predictions (`--reject-leakage` fails on [shared projects or texts](validation.md#benchmark-leakage-disclosure)); optional [frozen triage policy](diagnostic-triage.md) or `--profile diagnostic-corpus` for a [corpus development export](diagnostic-corpus.md#benchmark-input-profile) |
| `ahl apply RUN [--project DIR] [--authorize-plan SHA256]` | Review the saved change plan of an improvement run (read-only); with the exact plan hash, apply only an independently reproduced skill-header repair ([details](one-call-improvement.md#review-and-apply-a-saved-plan)) |
| `ahl adapters stage PROJECT --manifest FILE --candidate SPEC --adapter-bytes FILE [--authorize-spec SHA256]` / `ahl adapters rollback PROJECT --manifest FILE --deployment ID [--execute]` | Review a candidate presentation adapter against a controller-owned manifest and, with its exact spec digest, stage it through a private journal; preview or restore the previous version. Offline, executes no adapter code, installs no hook, see [stage a presentation adapter](stage-an-adapter.md) |
| `ahl compare --experiment FILE --output DIR [--public]` | Render a baseline/candidate experiment record as a static offline HTML review page, private unless `--public` exports approved synthetic or public data without events ([details](side-by-side-reports.md#static-viewer-ahl-compare), [public export](side-by-side-reports.md#public-export)) |
| `ahl score [project] --spec FILE --records FILE [--area ID ...] [--sessions FILE] [--format json\|md]` | Print one row per declared capability area from a recorded paired experiment, each with an evidence state (`scored`, `provisional`, `no_evidence`, `blocked`); `--sessions` adds an advisory, observed-only placement of recorded sessions that is never scored; read-only, no model call, see [score a harness per capability area](score-by-capability-area.md) and the [reference](score-reference.md) |
| `ahl score-sessions PROJECT --capture FILE --format claude\|codex --output FILE` | Export private, content-free advisory signals from a pinned native capture into a new file; offline, see [recorded sessions](score-by-capability-area.md#see-where-recorded-sessions-look-like-they-happen) |
| `ahl trace-import --format atif --input FILE --output NEW_DIR` | Convert one named ATIF-v1.8 trajectory into private AHL metadata events and a loss report; bodies, arguments, results, costs and extensions are dropped and counted, the outcome and lineage stay unknown, and nothing referenced is opened; offline, see [import an ATIF trajectory](import-atif-trajectory.md) and the [reference](atif-import-reference.md) |
| `ahl trace-export --format otel-json --input RUN_DIR --output NEW_DIR` | Export private metadata as local OTLP JSON logs with an explicit loss report; offline, see [OTLP export](otel-export.md) |
| `ahl memory init\|status\|verify\|reindex\|gc --store DIR` | Create and maintain an opt-in private evidence store; offline, see [private memory store](memory-store.md) |
| `ahl memory history --history-store DIR` | Read-only list of existing evaluation histories by reference with live authority checks; see [history index](memory-store.md#list-existing-evaluation-histories) |
| `ahl memory snapshot --checkout DIR --runtime R --version V` · `ahl memory diff --from-snapshot FILE --to-snapshot FILE` | Name the declared harness surfaces of a checkout and compare two saved snapshots; offline, read-only, no memory store, see [harness snapshots](harness-snapshots.md) |
| `ahl sources claude pin-check --events FILE` | Compare one Claude Code capture with the verified profile contract by field name only; read-only, see [verify a new Claude Code release](verify-claude-release.md) |
| `ahl sources claude-mem tool-check --tools FILE` | Compare one saved Claude-Mem MCP `tools/list` result with the pinned v15.9.0 read tools by input-schema structure; offline, no worker contact, see [Claude-Mem tool listing check](source-contracts.md#saved-mcp-tool-listing-check-claude-mem) |
| `ahl sources mempalace capabilities [--tools-list FILE]` | Print the offline MemPalace profile and tool capability report, optionally comparing a saved `tools/list` result by name only; no server, network or model, see [MemPalace capability report](source-contracts.md#mempalace-capability-report) |
| `ahl doctor [project] --profile offline --json` | Check readiness without running an agent; checks and boundaries are in [offline readiness](onboarding.md). Its non-required `claude_runtime_lag` warning reads dotted version strings from at most 200 Claude session transcript headers, under every profile |
| `ahl skills --agent claude` | Link AHL's own installed skills into a selected runtime's skill directory; it does not create, update or remove a target project's skills ([bundle review](one-call-improvement.md#validating-staged-skill-bundles)) |

Run `ahl COMMAND --help` for the complete options. Low-level evaluation supports
`--agent claude` or `--agent codex`; preparation and evaluation never mix runtimes
within a pair. CLI executables can be selected with `--claude` or `--codex`.

The installed skill also works inside an existing agent session. It discovers
improvement candidates itself; an incident description or proposed candidate
is optional. In Claude Code its command is `/harness-self-improvement`; it also
carries the evaluation methodology ([why one skill](single-skill-rationale.md)).
During a CLI-managed run, the controller owns execution and adoption; the skill
must not launch recursive improvement controllers.
