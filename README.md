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

`ahl analysis .` is an alias. This one call runs directly-follows process mining,
recurring-sequence detection and state-aware duplicate-observation checks. The JSON
receipt and private report list executed algorithms, evidence gaps and the next
action. Aggregate progress goes to stderr; stdout stays JSON.
See [what analysis runs](docs/analysis.md) for algorithm details and limits.

With a supported, authenticated Codex or Claude Code runtime and a trusted project,
request candidate preparation and evaluation:

```sh
ahl improve .
```

This may incur model costs. It writes private run artifacts and reports the result;
current CLI behavioral candidates remain recommendations. Use `ahl improve --help`
for runtime selection and budgets, and [workflows](docs/workflows.md) for details.
See [CLI help for agents and automation](docs/cli-help.md) for safe discovery,
input/output conventions and nested command help.
You can call `ahl improve .` directly: it collects and analyzes evidence itself.
Running `analyze` first is optional.

What `improve` and `watch` themselves write, and what AHL never writes (their agent is
cooperative, not sandboxed):

- `ahl improve .` writes private state only: staged baseline and candidate copies,
  evaluation results, a change plan and a run lock. AHL itself never writes project
  instructions, skills or hooks. A candidate that passes evaluation is reported for
  independent certification; it is not applied.
- The agent that `improve` starts is cooperative. It is told to treat the project
  as read-only, but nothing enforces that; your agent's own permission settings
  decide whether it can write there. AHL rechecks the project harness before it
  recommends and rejects a stale result, but it does not revert such a change.
- `ahl improve . --repair-contracts` is the one exception: it may apply a bounded
  skill-header repair, without model calls and with a rollback journal. A project
  `ahl.json` keeps even that repair recommendation-only.
- `ahl watch .` reruns the same recommendation-only staging and evaluation when
  evidence or execution configuration changes, or after a classified transient
  failure. It writes run artifacts and a private checkpoint, has no
  `--repair-contracts` option and never writes harness files itself; its agent is
  cooperative in the same way.
- Neither command commits, publishes, deploys or starts a background service.

Already working inside an agent session? Invoke the installed
`harness-self-improvement` skill and ask it to improve this project's harness.
The skill describes an assisted evaluation-and-edit loop; it is distinct from the
CLI's current recommendation-only controller. Supported autonomous application
and matching CLI/skill receipts are tracked in [#77](https://github.com/mihazs/agent-harness-lab/issues/77).

## Harness surfaces

AHL inventories eleven harness surfaces of Claude Code 2.1.317 and Codex 0.166.0
read-only: it never executes a hook, workflow or script while analyzing, and it
recommends changes to another repository instead of editing it in place. `agent_harness_lab/surfaces.py` owns each verdict.
For a synthetic path inside the checkout, the writability and the route a change gets:

| Surface | Claude Code | Codex |
| --- | --- | --- |
| `instructions` | `adoptable` / adopt | `adoptable` / adopt |
| `path_rules` | `adoptable` / adopt | `recommendation_only` / recommendation |
| `hooks` | `contract_required` / contract | `contract_required` / contract |
| `workflows` | `contract_required` / contract | no file surface |
| `agents` | `adoptable` / contract | `adoptable` / contract |
| `commands_skills` | `adoptable` / adopt | `adoptable` / adopt |
| `plugins` | `recommendation_only` / recommendation | `recommendation_only` / recommendation |
| `mcp` | `recommendation_only` / recommendation | `recommendation_only` / recommendation |
| `output_styles` | `recommendation_only` / recommendation | `recommendation_only` / recommendation |
| `memory` | `private_excluded` / excluded | `private_excluded` / excluded |
| `settings` | `recommendation_only` / recommendation | `recommendation_only` / recommendation |

A candidate may change an *adopt* surface in its staged copy; application still
follows the policy above. A *contract* change stays unresolved until a passed hook
contract names it; agent definitions take this route because their frontmatter can
register hooks. A *recommendation* is reported, never written. The inventory never
reads, hashes or changes an *excluded* (`private_excluded`) file. It lists one as
`not_read` only when its kind is recognized, such as `CLAUDE.local.md`; memory and
cache paths are skipped and never listed, so a missing memory node does not show
that no memory file exists. An *unsupported* path is never adopted or written, yet
the inventory may still read and hash it.
Change plans list the surfaces they leave for later as deferred.
`ahl sources inventory CHECKOUT --runtime RUNTIME --version VERSION` prints this
matrix for the declared runtime, and none for a version other than the pin.
See the [surface reference](skills/harness-self-improvement/references/surfaces.md)
for paths, version pins and unverified items, and [trial isolation](skills/harness-evaluation/references/isolation.md)
for what an evaluation trial still inherits.

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
| Analyze traces or compare a candidate | [Commands and workflows](docs/workflows.md) · [Command overview](docs/command-reference.md) |
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

Commit subjects, pull request titles and merge commits follow
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/); `task setup`
installs a `commit-msg` check and CI checks pull requests. See the
[commit convention](CONTRIBUTING.md#conventional-commits).

Versions use Changesets; do not change version numbers manually. For a releasable
change, run `task setup` and `task changeset`; see [release conventions](CONTRIBUTING.md#versions-and-changelogs).

Use `task setup:test` in a virtual environment, then `task test` to collect both
native suites once and distribute tests across CPU cores. Reusable commands belong
in `Taskfile.yml`; existing unittest cases are collected directly by pytest.
`task setup:test` also installs the pinned [ruff](https://docs.astral.sh/ruff/) linter.
`task lint` checks the tree (pyflakes and syntax errors, configured in `pyproject.toml`),
and the lefthook pre-commit hook runs it on staged Python files.

```sh
python3 -m venv .venv
task setup:test PYTHON=.venv/bin/python
task test PYTHON=.venv/bin/python
task lint PYTHON=.venv/bin/python
```

Set `TEST_WORKERS=4` to select a worker count, or `TEST_WORKERS=0` for debugging.
CI tests Python 3.14; package installation still requires Python 3.11 or newer.

Licensed under [MIT](LICENSE).
