# Agent-backed workflows and CLI reference

[Documentation index](README.md) · [First experiment](getting-started.md)

These workflows may use your authenticated runtime and incur inference costs.
Try the offline diagnosis first. Experimental limitations below apply throughout.

## Analyze, then improve

Use `ahl analyze .` (or `ahl analysis .`) for read-only analysis, and `ahl improve .`
for candidate preparation and evaluation. Improve runs the same analysis itself;
there is no required `mine` or `diagnose` step. See [analysis methods](analysis.md)
for which algorithms execute and which optional tools remain separate.

Both commands report aggregate stages on stderr and keep stdout as one JSON
receipt. Read the receipt's `analysis` object for executed methods, counts and
evidence gaps. `empty` means no events or prior evaluation records were found;
`incomplete` means collection caveats or invalid timestamps were observed;
`observed` describes the available metadata, without certifying completeness,
task success or independent roots. A zero finding count is not a clean bill of health.

For a cooperative `ahl.json` configuration with an explicitly pinned project task
bank, `ahl improve` freezes the bank before preparing a candidate and evaluates
all its tasks without an evaluation-author session. The bank must provide
development, regression and held-out coverage; the configured trial budget must
cover one preparation plus two trials per task. A fixed bank is used for one
candidate only, without automatic refinement. Conflicting runtime overrides,
changed bank contents, and combinations with managed/repeated experiment modes
are rejected. Managed banks require their controlled workflow. Results remain
recommendations; this path establishes neither certification nor isolation of
the cooperative runtime.

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

1. **Collects evidence.** Finds local captures whose recorded working
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

`--agent auto` uses the configured runtime or the one unambiguous installed runtime.
If both are installed, specify
`--agent codex` or `--agent claude` to pin the runtime. Authentication, model settings,
permissions and hooks come from that runtime. Permission bypass is never enabled.
The default whole-run budget is 45 minutes and 24 shared preparation/task roles;
project configuration can narrow these limits. Use `--timeout SECONDS` to set a smaller budget.
Input/configuration preparation and freezing consume that budget too. Evaluation
inherits the controller's original deadline, so a later stage cannot restart the
clock; exhausted preparation prevents new runtime probes or model trials.

On Linux with working bubblewrap namespaces, add `--sandbox-graders` to `improve`
or `watch` to run authored grader controls and trial grading in fresh offline
namespaces. The controller freezes every complete grader bundle before any
control runs and extracts only declared artifacts. An unavailable boundary blocks
before agent execution; grading never falls back to host execution. Optional
`--cgroup-root`, `--image-root` with `--image-sha256`, and `--disk-bytes` select
the existing resource/image profiles for both control and trial graders.
Agents still use the cooperative native CLI, inherited authentication and hooks;
this option does not establish full role isolation or authorize promotion.

`--agent responses --inference-settings FILE` selects the controlled workflow on
supported Linux hosts. Supply explicit cgroup, pinned-image and disk profiles;
the same client/request/tool/deadline budget covers proposer, evaluator author,
paired task execution and any refinement. Roles receive separate bounded views;
the author receives baseline/curated metadata and methodology. The original source
project and prior held-out feedback stay outside those views. Native discovery or
execution never becomes a fallback. Read the [role, receipt and resource contract](../skills/harness-evaluation/references/execution.md#controlled-improvement-roles)
before using an API account. Model compatibility/quality and independent
certification remain unverified; behavioral repairs still require review.

Candidate snapshots cover `AGENTS.md`, `CLAUDE.md`, project-local
`.agents/skills/` and `.claude/skills/`, and an existing project-owned `skills/` tree.
Symlinked/global skills and runtime permission settings are outside adoption scope.
Behavioral candidates remain recommendations. The explicit `--repair-contracts`
mode can apply a narrowly supported skill-header BOM repair with journaled rollback;
it does not run behavioral optimization or certify native activation.
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
| `ahl benchmark FILE --method baseline` | Compare incident-classifier predictions; optional [frozen triage policy](diagnostic-triage.md) |
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

### Analyze an explicit ai-memory session

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
coverage limitations and rows elided from other scopes. Bodies are excluded from
normalization; names, call linkage and outcomes absent from structured source
metadata remain unknown. These observations cannot certify a no-defect outcome
or grant permission to apply a candidate. Use an explicitly supported native
capture when structured diagnostic metadata is needed.
CLI progress reports aggregate page/observation counts on stderr during retrieval
and readback; stdout remains the JSON receipt and library calls are silent by default.

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
synthetic examples for public reports. Default collection reads scoped local
captures; `--memory-manifest` explicitly opts into scoped ai-memory network reads.
The configured agent may inspect the selected
project's source/instructions and metadata as part of its task; it uses your existing
inference configuration. The release builder includes tracked application files
and skills only, excluding local artifacts and memory routing files.
