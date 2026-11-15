# Instruction-adherence reference

Contracts of `ahl instruction-adherence`: its inputs, the sentence shapes it can check,
how a rule is judged against a session, and the report. For the steps see
[find where agents ignored instructions](detect-ignored-instructions.md).

```text
ahl instruction-adherence SESSIONS [--rules LAYER=FILE]... [--scope project|global|project+global]
                          [--output FILE] [--private-detail]
ahl instruction-adherence --native claude|codex [--project DIR] SESSION_FILE... [same options]
```

Exit: 0 report produced; 2 invalid input. The command is offline, read-only toward its inputs, uses
no model and no network, and its report profile is `ahl-instruction-adherence-v1`.

## Sessions file

Profile `ahl-instruction-sessions-v1`: at most 5,000 sessions and 16 MiB. Unknown fields are refused.

| Session field | Rule |
| --- | --- |
| `session` | Required, distinct, at most 256 characters; reported only as a digest |
| `turns` | Required list of at most 20,000 turns |
| `capture` | `complete`, `partial` or `unknown`; absent is `unknown` |
| `root` | Optional lineage label; absent means unknown lineage |
| `project_dir` | Optional absolute path; lets absolute call paths be compared with project-relative rules |
| `compactions` | Optional list of strictly increasing integers in `0..len(turns)`: each is the index of the first turn after a compaction. Absent means the session does not record compactions; `[]` means it records them and found none, which is not proof that none happened |
| `loaded` | Optional map of `LAYER:FILE-NAME` to `loaded`, `truncated`, `not_loaded`, `unknown` or `{"state": ..., "turn": N, "before": ...}` (loaded from turn N; `before` is what the earlier turns are, `not_loaded` by default or `unknown` when the capture cannot show whether the file was already in context) |

A turn is `{"role": "user"|"agent", "text": ...}` or `{"role": "tool_call", "tool": ..., "command": ..., "path": ...}`
where `command` and `path` are optional; each text is at most 64 KiB.

## Native session files

With `--native RUNTIME`, each path is a native session file (JSONL) of `--project` (default: the current
directory) and is projected into one session in memory; nothing but the turns below is kept.

| Rule | Behavior |
| --- | --- |
| Release | Claude Code: a release in the verified range of the telemetry adapter. Codex: the pinned `0.166.0` rollout. Anything else is skipped as `unsupported_version` |
| Project | The first recorded directory must be the project or inside it, else `foreign_project`. A subagent transcript is `subagent_transcript`; two sessions in one file are `mixed_sessions`; no session record is `no_session` |
| Files | A missing, linked, multi-linked or changed file is `unavailable`; over 128 MiB or a line over 8 MiB is `oversized`; invalid or duplicate-key JSON is `malformed`; at most 200 files, the rest `over_file_limit`, and about 512 MiB in all, the rest `over_byte_budget`; a second file of one session is `duplicate_session` |
| Turns | User prompts, agent replies and tool calls. Injected meta rows, compaction summaries and tool results are not turns. A prompt is clipped to its first 64 KiB, a reply to its last 64 KiB (where a question sits), and a command or path over 64 KiB is dropped so the call is undecidable |
| Claude calls | `command` from the tool input, and `path` from `file_path`, `notebook_path` or `path` |
| Codex calls | `exec_command` `cmd`; `shell` and `shell_command` `command` (a `bash -lc` wrapper is read as its script); `apply_patch` one call per file named in the patch; arguments that do not parse leave the call without a command |
| Capture | Always `partial`; lineage (`root`) is always unknown |
| Compaction | Claude: a `compact_boundary` system row or a compaction summary row records a compaction at the next turn's index; the boundary and its summary are one compaction, and both rows are checked like any other row of the capture (release, subagent, session). Codex: a `compacted` row or a `context_compacted` event, likewise counted once. The session always carries `compactions`, empty when none was read |
| Loading | Claude: a memory attachment for the project-root `AGENTS.md` or `CLAUDE.md` marks that file `loaded` from that turn only when the text it carries is exactly the text of the file the report judges; the turns before that attachment stay `unknown`, never `not_loaded`, because a file loaded at launch has no attachment event. An attachment with no text, other text, or for a file that is not judged leaves the file `unknown`. Codex: none recorded, so every rule stays unknown. Any other file is not recorded |

The Codex call fields and the Claude memory attachment are assumptions checked against synthetic fixtures,
like the telemetry adapters; a call whose shape differs is undecidable, never a violation.

`ahl instruction-adherence --native` refuses the run (exit 2) when any named file is skipped.

## Analysis and improve

`ahl analyze` and `ahl improve` read the native sessions they selected (default discovery or `--events`
with a pinned `--format`; a `--source-root` discovery is not read) and write `instruction-adherence.json`
(the report above, redacted, byte-identical for the same inputs) when at least one session was read. Rules
are the project-root `AGENTS.md` and `CLAUDE.md`, never a linked file; with `--scope` naming `global` the
user-level rule files are not read and the section lists `rule_layers_not_read: ["global"]`.

The brief's `instruction_adherence` section is counts only: `status` (`completed` or `not_run`), for
`not_run` a `reason` (`no_native_capture_path`, `no_readable_native_session`, `adherence_input_rejected`)
with `skipped` reason counts, and for `completed` the sessions read, `findings` by status, `abstentions`,
findings by `mechanisms`, the number of rule files read and the artifact name. `not_run` is never zero findings.

## Instructions it can check

Extraction is lexical and identical for harness files and user prompts. A sentence is
`machine_checkable` only when one of these shapes names an operand: a backticked command or tool
(a built-in tool name or `mcp__server__tool` is a tool, anything else is the leading tokens of a shell
command) or a path.

| Shape | Rule kind | Mechanism when violated |
| --- | --- | --- |
| `never` / `do not` / `must not` + run or use, edit or write | `forbidden` | `forbidden_action_taken` |
| `always` / `must` + run or use | `required` | `required_step_skipped` |
| `always run X before running Y` | `before` | `order_violated` |
| `ask (me) before` + run or edit | `gated` | `approval_not_sought` |
| `only edit` / `only work in` + path | `scope` | `scope_exceeded` |
| a user sentence that is just `stop` or `wait` | `halt` | `stop_not_honored` |

A hard cue (`never`, `must`, `always`, `only`, `ask before`) without an operand is `reviewable_only`; a
soft cue (`should`, `prefer`, `avoid`) is `unverifiable`. Globs, `..` paths and commands that do not parse
are never guessed. A harness file may hold 512 instructions in total; a user turn is read up to 512 and a
session up to 200 user instructions.

## Judging a rule

Results are three-valued: `violated`, `satisfied` or `unknown`. A shell call is split on `&&`, `||`, `;`,
`|` and newlines, wrappers such as `sudo` and `VAR=value` prefixes are skipped, and a call whose command text
is missing or does not parse is undecidable.

- A prohibited call, an out-of-scope edit or a call after `stop` is a violation even in a partial capture.
- A skipped required step, an ordering failure or an unapproved gated call is a violation only on a
  `complete` capture; on a partial one it stays unknown. A required step that was seen is `satisfied` even in
  a partial capture, because an observed call is evidence; every other `satisfied` result rests on absence and
  needs a complete capture.
- A required step is judged only when the rule's window holds a state-changing call (`Edit`, `Write`,
  `MultiEdit`, `NotebookEdit` or `apply_patch`). A question-only, read-only, search-only or shell-only session
  may owe the step nothing, so whether the rule applied is unknown and the decision is `undecided_no_trigger`.
- A directory operand matches itself and every path under it, whether or not it is written with a
  trailing slash; a file operand matches only that file.
- A user instruction ends at a later user turn that names the same operand with a supersession cue
  (`actually`, `go ahead`, `you can`, `never mind`); a violation before that turn still counts.
- A later user turn that repeats the instruction, or names it with `I told you`, `as I said` or `again`,
  restates it, unless the agent's last word before it was a question.
- A later user turn that names an operand the instruction constrains is also a reaction to it, never a
  verdict, when it is one of these: a **revert request** (`revert`, `undo`, `roll back`, `restore`,
  `put it back`), a **reopened task** (`not done`, `not fixed`, `still failing`, `you forgot`, `you
  missed`, `redo`, `try again`) or a **steering message** (a user turn that comes while the agent was
  acting, that is, the turn before it is a tool call and not the agent's own words). A reaction that names
  nothing the rule constrains, a reply straight after the agent's question, a reaction at or after a
  supersession and a reaction to a harness rule are not read. Only an instruction with a checkable operand
  is read this way.
- When a rule is violated, a reaction that came after its first violating call is listed in the finding's
  `corroboration` (`user_revert_requested`, `user_task_reopened`, `user_steering_message`,
  `user_restated`, in that order). When the capture cannot decide the rule, the reactions are one
  `possible_violation` per instruction: the first of `revert_requested`, `task_reopened`,
  `steering_message` and `instruction_restated` names its `mechanism` and the others are its
  `corroboration`. A rule that was satisfied on a complete capture has no finding.
- Two rules in force together that require and forbid the same operand are a `conflict`; neither is then
  reported as violated.

## Report

| Field | Meaning |
| --- | --- |
| `findings[]` | `status`: `observed_violation`, `possible_violation`, `not_loaded` or `conflict`; `direction`: `harness`, `user_prompt` or `mixed`; `mechanism`, `instruction_ref`, `source_layer`, `loading`, `session_ref`, `root_ref`, `lineage`, `span` (first and last turn), `occurrences`, `turns`, `corroboration`, `compaction`, `evidence_sha256` |
| `abstentions[]` | Findings whose status is below the calibrated threshold, with `abstention: below_calibrated_threshold` |
| `decisions` | Counts of rule-by-session decisions: `violated`, `satisfied` and each `undecided_*` reason, so unknowns stay visible |
| `recurrence[]` | Per instruction, mechanism and status: `sessions`, `independent_roots`, `unknown_lineage_sessions`, `occurrences` |
| `sessions` | Totals, partial and unknown-capture counts, declared roots and unknown-lineage sessions |
| `inventory` | Per source: counts of machine-checkable, reviewable and unverifiable instructions |
| `calibration` | The measurements below |

`compaction` says whether a recorded compaction fell after the instruction came into view (a harness rule's loading turn, a prompt's own turn, or the launch when the violating call came before the recorded loading turn and what came before is `unknown`: the rule may have been in view since launch) and by the last turn of the finding's `span`: `in_window`, `none_in_window` or `not_recorded` (the session has no `compactions`). It is a fact about the record, not a cause, and it never changes a finding's identity, status or counts.

One finding is kept per instruction, mechanism and session; each further violating call raises `occurrences`
and is listed in `turns` (at most five). A skipped required step, a `conflict` and a user reaction (a restated
instruction, a revert request, a reopened task or a steering message) are one occurrence each, with no
`turns`: their `span` bounds the window, the two instruction turns or the prompt and its reaction. Counts of independence use `root` only: sessions sharing a root are one independent root, and
sessions without one are reported under `unknown_lineage_sessions` and never added to `independent_roots`.

The report states `authority: descriptive_only`, `causal_diagnosis: false` and `publication_allowed: false`.
It is redacted by default (`redaction: digest_only`): every reference is a digest and spans are turn
numbers. `--private-detail` adds only the instruction sentence, at most 240 characters.

## Calibration

The detector is measured on a fixed synthetic sample shipped in
`agent_harness_lab/diagnostics/adherence_calibration.py`. Each case carries a label (`violation`,
`no_violation` or `cannot_tell`) written with the case; the labels are authored fixtures, not independent
human review and not real sessions.

- `precision`, `recall` and `abstention_rate` each report `hits`, `total`, `value` and a 95% Wilson interval.
  A `cannot_tell` answer counts against recall and never toward precision.
- A claimed status is admitted to `findings` only when its sample precision has a Wilson lower bound of at
  least 0.8 over at least 10 cases (`by_status`, `admitted`); otherwise its findings are `abstentions`.
  With the shipped sample, `observed_violation` is admitted and `possible_violation` is not; the sample
  holds reactions the detector cannot tell apart (a revert request about an edit the agent made and one
  about an edit the user made), which is why a reaction without checkable evidence is an abstention.

The sample measures that the documented rules behave as documented. It does not estimate accuracy on real
sessions, which have wording and tool use the sample does not model.
