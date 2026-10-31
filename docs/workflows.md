# Agent-backed workflows and CLI reference

[Documentation index](README.md) · [First experiment](getting-started.md)

These workflows may use your authenticated runtime and incur inference costs.
Try the offline diagnosis first. Experimental limitations below apply throughout.

## Automatic improvement

In your project, run:

```sh
ahl improve
```

Or point it at a project directory:

```sh
ahl improve /path/to/project --agent claude
```

The command automatically:

1. **Collects evidence.** Finds bounded local captures whose recorded working
   directory matches the exact project, and reads existing evaluation failures
   under that project's `artifacts/` directory. It does not query ai-memory.
2. **Identifies candidates.** Uses process analysis plus the selected agent to
   inspect repeated routines, observed tool errors, failed evaluations, current
   instructions, successful controls, and competing explanations. When capture is
   absent, the agent can inspect current source/contracts; it must not invent history.
3. **Stages repairs.** Copies project instructions and project-owned skills to
   baseline/candidate snapshots. The preparation agent edits the candidate. A separate fresh session builds
   isolated tasks and artifact graders from baseline/evidence, without the patch
   in its prompt. A failure description is optional.
4. **Checks the experiment.** Requires broken, partial, and complete grader controls,
   development/regression/held-out tasks, and frozen evaluator inputs. Baseline and
   candidate run under the same selected CLI. A failed candidate may be refined once
   with fresh held-out inputs, within the shared resource ceiling.
5. **Reports eligible candidates.** A descriptive comparison requires a reproduced baseline development failure,
   passing candidate tasks, passing baseline regression controls, and unchanged inputs.
   Behavioral candidates remain recommendations while role isolation, audited lineage,
   repeated certification and effective runtime verification are unavailable.
   Ties, incomplete results, and failed checks leave project files unchanged.

An optional hint can focus the investigation without replacing discovery:

```sh
ahl improve --focus 'Look especially at repeated checks and recovery loops'
```

`--agent auto` chooses an available Codex CLI first, then Claude Code; specify
`--agent codex` or `--agent claude` to pin the runtime. Authentication, model settings,
permissions and hooks come from that runtime. Permission bypass is never enabled.
The default whole-run budget is 45 minutes, with at most four planning sessions and
20 task trials. Use `--timeout SECONDS` to set a smaller budget.

Automatic adoption is limited to `AGENTS.md`, `CLAUDE.md`, project-local
`.agents/skills/` and `.claude/skills/`, and an existing project-owned `skills/` tree.
Symlinked/global skills and runtime permission settings are outside adoption scope.
The command does not commit, publish, deploy, or install a background service.

Private analysis, candidate snapshots, evaluator traces, and original files remain
under the user state directory (`~/.local/state/agent-harness-lab/runs/` by default).
The command prints the run directory and disposition. Use a new `--output` directory
if you need another location; keep it out of version control.

## Optional continuous monitoring

```sh
ahl watch /path/to/project --agent claude --interval 300
```

This foreground monitor performs an initial improvement pass, then checks evidence
at the interval. It runs improvement again only when the accepted project evidence
changes. Versioned checkpoints persist across restarts. Completed rejection/no-change runs
are not repeated on unchanged evidence. Only explicitly classified transient
infrastructure failures may retry, with backoff and at most three total attempts.
Authentication, setup, interrupted and unknown failures require intervention or a
relevant execution-configuration change. Legacy attempted fingerprints remain
suppressed. Corrupt checkpoints stop monitoring and remain available for inspection. A per-project lock prevents overlapping improvement runs.
Press Ctrl-C to stop. `--once` performs one check and exits.

Each triggered pass can use the configured model and the same per-run time budget
as `ahl improve`. Installation does not start monitoring. To rerun deliberately on
unchanged evidence, use `ahl improve`; stale locks require checking the recorded PID
before removing them. `--max-attempts` (1–10), `--retry-delay` (1–3600 seconds),
and `--watch-budget` control retries; the default total execution allowance across
attempts is the configured `--timeout`, not that allowance multiplied per retry.
`watch --once` reports the last disposition even when no new run is eligible.
Exit codes: 0 completed, 1 failed/incomplete (including budget exhaustion and
interruption), 2 invalid input/setup, 3 inconclusive/blocked. Unknown statuses fail
closed with exit code 1. These codes apply to `improve` as well.

## Inspect before improving

```sh
ahl analyze /path/to/project
```

This runs local analysis and writes a report without starting an agent or changing
project instructions. Native discovery is bounded and may miss older captures.
Missing evidence, uncertain lineage and unknown outcomes are reported explicitly.

You can also supply an explicit redacted input:

```sh
ahl analyze --events /path/to/events.jsonl --format normalized
ahl improve --events /path/to/redacted-transcript.jsonl --format claude
```

Explicit input replaces automatic capture discovery. Supported formats are
`normalized`, `codex`, `claude`, and `ai-memory` exports. Native normalization exports
visible metadata rather than message bodies, arguments, results or thinking blocks.
Inputs still require redaction; these tools are not general-purpose sanitizers.

## Commands

| Command | Purpose |
| --- | --- |
| `ahl improve [project]` | Discover issues, stage repairs and evaluate; behavioral adoption is blocked |
| `ahl watch [project]` | Repeat improvement on changed evidence |
| `ahl analyze [project]` | Automatically collect and analyze evidence without an agent |
| `ahl diagnose --events FILE --output DIR` | Diagnose an explicit event file |
| `ahl mine FILE` | Mine event transitions and recurring root-case support |
| `ahl evaluate --manifest FILE --baseline DIR --candidate DIR --output DIR` | Execute a prepared baseline/candidate experiment |
| `ahl benchmark FILE --method baseline` | Compare incident-classifier predictions |
| `ahl skills --agent claude` | Link installed skills into a selected runtime's skill directory |

Run `ahl COMMAND --help` for the complete options. Low-level evaluation supports
`--agent claude` or `--agent codex`; preparation and evaluation never mix runtimes
within a pair. CLI executables can be selected with `--claude` or `--codex`.

The installed skills also work inside an existing agent session. They discover
improvement candidates themselves; an incident description or proposed candidate
is optional. In Claude Code their commands are `/harness-self-improvement` and
`/harness-evaluation`. During a CLI-managed run, the controller owns execution and
adoption; the skills must not launch recursive improvement controllers.

## Evidence and limits

**Experimental.** Automated preparation and descriptive evaluation are implemented;
automatic behavioral adoption is blocked pending certification prerequisites; broad gains
in agent success or real-world diagnostic accuracy have not been established.
Graders are constructed in a separate session and falsified with controls, but
remain cooperative code rather than independent semantic proof. Agent runtimes, inherited hooks, and graders are not a
hostile-code security sandbox. Use trusted projects and synthetic test fixtures;
keep production credentials and mutations out of evaluation tasks.

Repetition alone does not establish waste. Tool errors can be expected negative
tests. Fewer calls qualify as an improvement only when required behavior and
completion remain intact. No model is promoted on an easy synthetic smoke test.

- [Primary scientific sources and their limits](../skills/harness-evaluation/references/evidence-to-design.md)
- [Diagnosis and measurement criteria](../skills/harness-evaluation/references/trace-diagnosis.md)
- [Evaluation contract](../skills/harness-evaluation/references/execution.md)
- [Initial validation](validation.md)

## Privacy

Analyzed-project identities, memory contents, raw traces, and history-derived
findings must stay out of commits, releases and public issues. Use reproducible
synthetic examples for public reports. Automatic collection reads scoped local
captures, not the ai-memory server. The configured agent may inspect the selected
project's source/instructions and metadata as part of its task; it uses your existing
inference configuration. The release builder includes tracked application files
and skills only, excluding local artifacts and memory routing files.

