# Instruction-adherence reference

Contracts of `ahl instruction-adherence`: its inputs, the sentence shapes it can check,
how a rule is judged against a session, and the report. For the steps see
[find where agents ignored instructions](detect-ignored-instructions.md).

```text
ahl instruction-adherence SESSIONS [--rules LAYER=FILE]... [--scope project|global|project+global]
                          [--output FILE] [--private-detail]
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
| `loaded` | Optional map of `LAYER:FILE-NAME` to `loaded`, `truncated`, `not_loaded`, `unknown` or `{"state": ..., "turn": N}` (loaded from turn N) |

A turn is `{"role": "user"|"agent", "text": ...}` or `{"role": "tool_call", "tool": ..., "command": ..., "path": ...}`
where `command` and `path` are optional; each text is at most 64 KiB.

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
- Two rules in force together that require and forbid the same operand are a `conflict`; neither is then
  reported as violated.

## Report

| Field | Meaning |
| --- | --- |
| `findings[]` | `status`: `observed_violation`, `possible_violation`, `not_loaded` or `conflict`; `direction`: `harness`, `user_prompt` or `mixed`; `mechanism`, `instruction_ref`, `source_layer`, `loading`, `session_ref`, `root_ref`, `lineage`, `span` (first and last turn), `occurrences`, `turns`, `corroboration`, `evidence_sha256` |
| `abstentions[]` | Findings whose status is below the calibrated threshold, with `abstention: below_calibrated_threshold` |
| `decisions` | Counts of rule-by-session decisions: `violated`, `satisfied` and each `undecided_*` reason, so unknowns stay visible |
| `recurrence[]` | Per instruction, mechanism and status: `sessions`, `independent_roots`, `unknown_lineage_sessions`, `occurrences` |
| `sessions` | Totals, partial and unknown-capture counts, declared roots and unknown-lineage sessions |
| `inventory` | Per source: counts of machine-checkable, reviewable and unverifiable instructions |
| `calibration` | The measurements below |

One finding is kept per instruction, mechanism and session; each further violating call raises `occurrences`
and is listed in `turns` (at most five). A skipped required step, a `conflict` and a restated instruction are
one occurrence each, with no `turns`: their `span` bounds the window, the two instruction turns or the prompt
and its repeat. Counts of independence use `root` only: sessions sharing a root are one independent root, and
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
  With the shipped sample, `observed_violation` is admitted and `possible_violation` is not.

The sample measures that the documented rules behave as documented. It does not estimate accuracy on real
sessions, which have wording and tool use the sample does not model.
