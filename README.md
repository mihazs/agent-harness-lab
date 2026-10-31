# Agent Harness Lab

**Find recurring agent failures. Test the fix. Keep what works.**

Two reusable skills for **Claude Code and Codex**, plus local tools for improving agent instructions,
workflows, and skills with observable evidence.

Use it when an agent repeats the same mistakes, spends too long rediscovering a
routine, loses the objective between steps, or follows instructions that no longer
match the code. Turn those observations into a focused experiment before adding
another rule to your harness.

[Install](#install-the-skills) · [Try it](#try-it-with-your-agent) ·
[Tools](#try-the-tools) · [Research](#research-and-current-status) ·
[Contribute](CONTRIBUTING.md)

## What you get

| Skill | Use it to |
| --- | --- |
| **harness-self-improvement** | Diagnose distinct failure mechanisms, propose small repairs, test candidates, and account for unresolved issues. |
| **harness-evaluation** | Compare baseline and candidate behavior with frozen graders, successful controls, regressions, and unseen tasks. |

The accompanying tools mine recurring event sequences, report evidence gaps, run
paired agent trials, and compare optional local incident classifiers. The default
Python tools use the standard library. No hosted model is required for offline
trace analysis.

## Install the skills

From the project where you want to use the skills:

```sh
npx skills add mihazs/agent-harness-lab --skill '*'
```

Choose Claude Code or Codex when prompted. Install **both** skills: they reference
one another. Add `-g` for installation across projects. The installer is the
[open-source skills CLI](https://github.com/vercel-labs/skills), which requires
Node.js 22.20 or later and supports multiple agent environments.

To install directly for Claude Code in the current project:

```sh
npx skills add mihazs/agent-harness-lab --skill '*' --agent claude-code
```

In Claude Code, invoke `/harness-self-improvement` or `/harness-evaluation`.

For updates or removal, use that CLI's `skills check`, `skills update`, and
`skills remove` commands. It manages installation separately from this repository.

### Prefer Python and a local checkout?

Requires Git and Python 3.11+. This route links both skills to the checkout:

```sh
git clone https://github.com/mihazs/agent-harness-lab.git
cd agent-harness-lab
python3 tools/link_skills.py
```

The default destination is `~/.agents/skills` for Codex. For Claude Code, install
both skills into `~/.claude/skills`:

```sh
python3 tools/link_skills.py --agent claude
```

For another directory, pass `--destination /path/to/skills` instead of `--agent`.

Keep the checkout in place. Editing it updates the linked skills immediately;
`git pull --ff-only` retrieves upstream updates. Existing same-name directories
are backed up below `~/.local/state/harness-improver/backups`; the installer prints
the backup location. Foreign symlinks and non-directory files are refused.
Rerunning an unchanged installation is a no-op.

To uninstall, remove only links still pointing to this checkout, then restore any
original directories from the printed backup location. Preserve intervening changes.

## Try it with your agent

After installation, start a fresh agent session in your project and ask:

> Use harness-self-improvement to investigate this recurring failure: [describe
> the observed failure]. Preserve unrelated work. Reproduce the issue, compare
> baseline and candidate, and report the evidence and remaining limitations.

For an existing candidate:

> Use harness-evaluation to compare these baseline and candidate instructions.
> Freeze the acceptance checks before scoring, include a successful control and
> a regression, and reserve a fresh task for transfer testing.

Provide a concrete failure, relevant code, and redacted evidence you are authorized
to analyze. The skills guide the agent; they do not configure telemetry or start a
background service. Actual agent trials need a working agent runtime. The bundled
runner supports an authenticated Claude Code or Codex CLI and retains the selected
runtime's existing configuration and hooks. It never enables permission bypass.
Contributors using Claude Code load the shared repository instructions through
`CLAUDE.md`, which imports `AGENTS.md`.

## How improvement works

1. **Observe:** identify recurring sequences, failed outcomes, corrections, and
   missing evidence. Distinguish task failures from tool or evaluator defects.
2. **Explain:** connect each proposed repair to a mechanism and an observable
   acceptance check. Compare failed cases with successful controls.
3. **Experiment:** execute baseline and candidate with comparable budgets and
   independent graders. Keep held-out feedback out of candidate refinement.
4. **Adopt or reject:** retain supported changes, reject regressions, and report
   what is unresolved. A tie does not demonstrate an improvement.

Repeated behavior is a lead to investigate. Fewer tool calls count as a benefit
only when completion and required checks remain intact. New instructions and
new skills must earn their place through observed outcomes.

## Try the tools

Clone the repository first if you installed only the skills. All commands below
run from the checkout and require Python 3.11+.

### See a diagnosis without providing private data

```sh
python3 tools/diagnose.py --events examples/events.jsonl --output artifacts/first-diagnosis
```

Open `artifacts/first-diagnosis/report.md` for the readable report or
`findings.json` for machine-readable results. The synthetic example contains three
cases with the same `inspect → validate` sequence. The report identifies that
recurrence and makes missing evidence visible; it does not label the sequence a
failure. Output directories must be new, so choose another name to rerun.

### Mine your own redacted events

Each JSONL row needs `case_id`, `event_id`, and `activity`. Optional fields include
`root_case_id`, `actor`, and a timezone-aware ISO `timestamp`.

```json
{"case_id":"example","event_id":"1","activity":"validate","actor":"tool"}
{"case_id":"example","event_id":"2","activity":"stop","actor":"agent"}
```

```sh
python3 skills/harness-evaluation/scripts/mine_traces.py events.jsonl --exclude unknown
python3 tools/diagnose.py --events events.jsonl --output artifacts/diagnosis
```

The miner separates raw adjacency from filtered, run-collapsed projections.
Diagnosis adds recurring short sequences, evidence coverage, and duplicate
observations when the necessary fingerprints exist. Inputs must already be
redacted: these tools are not sanitizers. Stops do not prove success, sequence
counts do not establish causation, and reported root lineage requires verification.
Diagnosis defaults to a 100,000-event limit and refuses silent truncation.

See the [operational criteria](skills/harness-evaluation/references/trace-diagnosis.md)
for input details and interpretation.

### Run a paired agent evaluation

Prepare skill snapshots, fixtures, and external graders using the
[execution contract](skills/harness-evaluation/references/execution.md):

```sh
python3 skills/harness-evaluation/scripts/run_evaluation.py \
  --manifest /path/to/manifest.json \
  --baseline /path/to/baseline --candidate /path/to/candidate \
  --output /path/to/new-results
```

Codex is the default. Add `--agent claude` to the same command for Claude Code;
use `--claude /path/to/claude` if the binary is not on your PATH. Keep the runtime
fixed within a paired comparison. Claude trials load snapshot skills through a
session-local plugin, under `/harness-trial:skill-name`, avoiding collisions with
unqualified personal skill names. Existing ambient skills can still influence
behavior; inspect traces before claiming the snapshot was used.

Claude transcripts can also be diagnosed offline:

```sh
python3 tools/diagnose.py --format claude --project example/project \
  --events redacted-transcript.jsonl --output artifacts/claude-diagnosis
```

Normalization preserves visible tool-call/result links, without exporting message
bodies, tool inputs, or thinking blocks. A tool error does not imply task failure.

Each trial gets a fresh workspace. Execution is sequential and timeout-bounded;
interruption terminates and reaps the owned child process group. Isolation is
cooperative, not a security or network sandbox. Do not put real credentials or
production mutations in fixtures.

### Optional: compare local classifiers

Use a virtual environment for the optional ML dependencies:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install '.[ml]'
mkdir -p artifacts
python examples/classifier_fixture.py > artifacts/classifier-fixture.jsonl
python tools/benchmark_models.py artifacts/classifier-fixture.jsonl --method baseline
python tools/benchmark_models.py artifacts/classifier-fixture.jsonl --method tfidf
```

Embedding and NLI methods require a local model directory via `--model-path`.
NLI also needs an atomic `--question`. Run `python tools/benchmark_models.py --help`
for the input contract and options. Keep related root cases in one split.
The synthetic fixture is deliberately easy: passing it verifies execution, not
real-world accuracy. Unknown labels stay out of scored metrics, and insufficient
calibration support produces abstention. No model is promoted on a smoke test.

## Research and current status

**Experimental, version 0.1.0.** Process mining, offline diagnosis, paired trials,
and local classifier adapters are implemented. General improvements in task
success or diagnostic accuracy have not been established.

The methodology draws on process mining, sentence representations, selective
prediction, calibration, and reflective prompt optimization. Each method must
answer a concrete question and outperform a simpler baseline on relevant evidence.
Research findings are hypotheses to test locally, not performance promises.

- [Primary sources and their limits](skills/harness-evaluation/references/evidence-to-design.md)
- [Diagnosis and measurement criteria](skills/harness-evaluation/references/trace-diagnosis.md)
- [Executed validation and remaining gaps](docs/validation.md)

## Privacy

Keep analyzed-project identities, memory contents, raw traces, credentials, and
history-derived findings out of commits and public issues. Use synthetic examples
for reports you intend to publish. Local results, model files, and trial workspaces
belong under ignored `artifacts/`; ignoring a directory does not sanitize its contents.

## Contribute

Bring a reproducible failure, a focused improvement, or a stronger falsifier.
Contributions that simplify the harness while preserving useful behavior are welcome.

```sh
python3 -m unittest discover -s skills/harness-evaluation/scripts -p 'test_*.py'
python3 -m unittest discover -s tools/tests -p 'test_*.py'
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) for verification expectations and
[SUPPORT.md](SUPPORT.md) for questions. Report vulnerabilities privately through
[SECURITY.md](SECURITY.md). Participation follows the
[Code of Conduct](CODE_OF_CONDUCT.md). Licensed under [MIT](LICENSE).
