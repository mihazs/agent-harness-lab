# Agent Harness Lab

**Find what your agent needs to improve. Evaluate the repair. Apply what passes.**

Agent Harness Lab discovers improvement candidates from local project traces,
evaluation failures, and the project's own harness. It stages repairs, checks them
against baseline behavior, and adopts eligible changes to project instructions
and skills. **You do not need to describe a failure first.**

Works with **Claude Code and Codex**. Includes an optional continuous monitor,
release installer, and the `ahl` command. The default installation uses Python's
standard library; Node.js is needed only by maintainers for Changesets.

[Install](#install-from-a-release) · [Automatic improvement](#automatic-improvement) ·
[Continuous monitoring](#optional-continuous-monitoring) · [Commands](#commands) ·
[Contribute](CONTRIBUTING.md)

## Install from a release

Requires Python 3.11+, a symlink-capable filesystem, and `~/.local/bin` on PATH.
Run the installer from the latest GitHub release:

```sh
curl -fsSL https://github.com/mihazs/agent-harness-lab/releases/latest/download/install.py | python3 -
```

The installer downloads the versioned release archive, verifies its SHA-256 checksum,
and installs **the CLI and both skills for Claude Code and Codex**. No Git checkout,
pip environment, or Node.js installation is required. The release installer source
is [install.py](install.py).

```sh
ahl --version
ahl --help
```

To install for just one runtime, append `--agent claude` or `--agent codex` after
`python3 -`. For a pinned release, append `--version 0.2.0`. Rerun the installer
to upgrade. Owned release links are updated transactionally; existing ordinary
skill directories are backed up. Foreign symlinks and unrelated `ahl` executables
are refused rather than overwritten. Versioned application files live under
`~/.local/share/agent-harness-lab/releases/`; the installer prints any backup path.

Already downloaded the assets? Use the release's `install.py` with
`--archive agent-harness-lab-VERSION.tar.gz --checksums SHA256SUMS`.
`--root` and `--bin-dir` select alternative application/launcher locations.

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
5. **Applies eligible changes.** Requires a reproduced baseline development failure,
   passing candidate tasks, passing baseline regression controls, and unchanged inputs.
   Copies only allowed harness files after checking for concurrent source changes.
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
| `ahl improve [project]` | Discover issues, stage repairs, evaluate, and adopt eligible changes |
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

**Experimental.** Automated preparation and adoption are implemented; broad gains
in agent success or real-world diagnostic accuracy have not been established.
Graders are constructed in a separate session and falsified with controls, but
remain cooperative code rather than independent semantic proof. Agent runtimes, inherited hooks, and graders are not a
hostile-code security sandbox. Use trusted projects and synthetic test fixtures;
keep production credentials and mutations out of evaluation tasks.

Repetition alone does not establish waste. Tool errors can be expected negative
tests. Fewer calls qualify as an improvement only when required behavior and
completion remain intact. No model is promoted on an easy synthetic smoke test.

- [Primary scientific sources and their limits](skills/harness-evaluation/references/evidence-to-design.md)
- [Diagnosis and measurement criteria](skills/harness-evaluation/references/trace-diagnosis.md)
- [Evaluation contract](skills/harness-evaluation/references/execution.md)
- [Initial validation](docs/validation.md)

## Privacy

Analyzed-project identities, memory contents, raw traces, and history-derived
findings must stay out of commits, releases and public issues. Use reproducible
synthetic examples for public reports. Automatic collection reads scoped local
captures, not the ai-memory server. The configured agent may inspect the selected
project's source/instructions and metadata as part of its task; it uses your existing
inference configuration. The release builder includes tracked application files
and skills only, excluding local artifacts and memory routing files.

## Contribute and release

For development, clone the repository and run `./ahl`. Python tools remain usable
without optional ML dependencies. Classifier experiments that need scikit-learn or
local transformer models can install the optional `.[ml]` extra in a virtual environment.

```sh
python3 -m unittest discover -s skills/harness-evaluation/scripts -p 'test_*.py'
python3 -m unittest discover -s tools/tests -p 'test_*.py'
```

Versions and changelogs are managed by **Changesets**. For a releasable change:

```sh
npm ci
npm run changeset
```

Commit the generated changeset with the change. CI opens a version PR that updates
`package.json`, Python metadata and `CHANGELOG.md`. After that PR is merged, CI
builds and publishes the versioned archive, installer and `SHA256SUMS` to GitHub
Releases. Nothing is published to npm. See [CONTRIBUTING.md](CONTRIBUTING.md).

Read [SUPPORT.md](SUPPORT.md) for questions and [SECURITY.md](SECURITY.md) for private
vulnerability reporting. Participation follows [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Licensed under [MIT](LICENSE).
