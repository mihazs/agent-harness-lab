# Agent Harness Lab

**Find problems in your coding-agent workflow. Test harness changes before adopting them.**

Agent Harness Lab (AHL) helps developers maintain `AGENTS.md`, `CLAUDE.md` and
project skills for **Codex and Claude Code**. Inspect local evidence, stage a
candidate change, and compare it with the original on executable tasks.

**Python 3.11+ · Standard-library core · MIT · Experimental**

[Install](#install) · [Try without credentials](#try-it-without-credentials) ·
[Use on your project](#use-on-your-project) · [Documentation](docs/README.md) ·
[Contribute](#contribute)

## Why use it?

- **Investigate repeated behavior:** inspect explicit traces and recurring patterns.
- **Check an instruction or skill change:** compare baseline and candidate outputs.
- **Keep the evidence:** review task results, limitations and local artifacts.

A repeated tool call is a clue, not proof of waste. A passing synthetic test is
not proof of a general improvement. The current CLI **stages, evaluates and
recommends; it does not automatically apply behavioral changes**.
[One-call application is being implemented](https://github.com/mihazs/agent-harness-lab/issues/77).

## Install

Python 3.11+, `curl`, a POSIX shell and symlink support are required:

```sh
curl -fsSL https://github.com/mihazs/agent-harness-lab/releases/latest/download/install.py | python3 -
```

Installs `ahl` and both bundled skills for Codex and Claude Code, without Git,
pip or Node.js. This executes the official release installer; see
[installation details](docs/installation.md) for inspection, pinned versions,
updates and custom locations. Nothing starts in the background.
If `ahl` is not on PATH, use `~/.local/bin/ahl --help` or add that directory to PATH.

## Use on your project

Start with local evidence discovery; this does not call a model:

```sh
ahl analyze .
```

With a supported, authenticated Codex or Claude Code runtime and a trusted project,
request candidate preparation and evaluation:

```sh
ahl improve .
```

This may incur model costs. It writes private run artifacts and reports the result;
default CLI behavioral candidates remain recommendations. Use `ahl improve --help`
for runtime selection and budgets, and [workflows](docs/workflows.md) for details.

Already working inside an agent session? Invoke the installed
`harness-self-improvement` skill and ask it to improve this project's harness.
The skill describes an assisted evaluation-and-edit loop; it is distinct from the
CLI's default recommendation-only controller. A separately signed
[conditional root instruction policy](docs/conditional-instruction-application.md)
can apply an existing `AGENTS.md` update through controlled Responses and a complete
protected final comparison. Configuration alone cannot authorize it. Broader
autonomous application and matching CLI/skill receipts remain tracked in
[#77](https://github.com/mihazs/agent-harness-lab/issues/77).

## Try it without credentials

Requires Git, Python 3.11+ and a POSIX shell. This uses the current source checkout;
the latest published release may not yet include the new example.

```sh
git clone https://github.com/mihazs/agent-harness-lab.git
cd agent-harness-lab
python3 -m agent_harness_lab --help
python3 -m agent_harness_lab diagnose --events examples/repeated-observation.jsonl --output /tmp/ahl-first-diagnosis
cat /tmp/ahl-first-diagnosis/report.md
```

Choose a new output path if `/tmp/ahl-first-diagnosis` already exists. Nothing is
installed globally. This command reads only the supplied synthetic event file,
writes the report locally and does not start an agent.

Actual report excerpt:

```text
Events: 4; cases: 1.
Observed findings: 1.
Recurring sequences: 0.
```

The finding is `duplicate_observation`: two read-only calls have matching tool,
input, result and recorded state. This is an **observed pattern**, not proof of
waste or a recommendation to remove a check. Outcomes and independence are unknown.
Read the [full output, controls and interpretation](docs/getting-started.md).

## Choose your next step

| Goal | Start here |
| --- | --- |
| Understand the example and its limits | [First diagnosis walkthrough](docs/getting-started.md) |
| Analyze traces or compare a candidate | [Commands and workflows](docs/workflows.md) |
| Understand what a result establishes | [Evaluation contracts](docs/evaluation-foundation.md) |
| Explore task banks and experiments | [Controlled experiments](docs/controlled-experiments.md) |
| Report a problem or ask a question | [Support](SUPPORT.md) |
| Help deliver autonomous optimization | [Priority implementation issue](https://github.com/mihazs/agent-harness-lab/issues/77) |

## Safety, privacy and evidence

- Use trusted projects and synthetic fixtures. Agent runtimes, inherited hooks and
  cooperative graders are **not a hostile-code security sandbox**. Optional
  [`--sandbox-graders`](docs/workflows.md#automatic-improvement) isolates artifact
  grading on supported Linux hosts; native agent execution remains cooperative.
- Real-world diagnostic accuracy and general performance gains are unestablished.
  Synthetic checks exercise implementation behavior; they do not certify improvement.
- Repetition is not waste. Fewer tool calls count as an improvement only when required
  behavior and outcomes are preserved.
- Keep private traces, project identities, history-derived findings, credentials and
  trial workspaces out of commits and public issues. Normalization is not sanitization.
- Agent-backed workflows use your runtime configuration and may incur model costs.
  Installation starts no background monitor.

[Evaluation contracts and pending safeguards](docs/evaluation-foundation.md) ·
[Implemented and pending experiments](docs/controlled-experiments.md) ·
[Scientific sources](skills/harness-evaluation/references/evidence-to-design.md) ·
[Initial validation](docs/validation.md) · [Scientific roadmap](https://github.com/mihazs/agent-harness-lab/issues/3)

## Contribute

New contributors are welcome. You can improve an unclear example, add a synthetic
reproduction, or tackle a focused implementation task. Browse
[good first issues](https://github.com/mihazs/agent-harness-lab/issues?q=is%3Aissue%20is%3Aopen%20label%3A%22good%20first%20issue%22)
and ask scope questions on the issue before starting a larger change.

Start with the [contributor guide](CONTRIBUTING.md) for setup, architecture, tests
and choosing a bounded task. Documentation, synthetic fixtures and focused tests
are useful contributions alongside research work.

Questions and reproducible bugs belong in [Issues](https://github.com/mihazs/agent-harness-lab/issues).
See [support](SUPPORT.md), [private security reporting](SECURITY.md)
and the [code of conduct](CODE_OF_CONDUCT.md). No response-time commitment is made.

Versions use Changesets; do not change version numbers manually. For a releasable
change, run `task setup` and `task changeset`; see [release conventions](CONTRIBUTING.md#versions-and-changelogs).

Use `task test` to run both native suites below. Reusable commands belong in
`Taskfile.yml`; source, wheel and archive checks share discoverable unittest cases.

```sh
python3 -m unittest discover -s skills/harness-evaluation/scripts -p 'test_*.py'
python3 -m unittest discover -s tools/tests -p 'test_*.py'
```

Licensed under [MIT](LICENSE).
