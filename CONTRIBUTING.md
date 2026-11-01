# Contributing

Documentation, focused tests, synthetic examples and research review are welcome.
Check [open issues](https://github.com/mihazs/agent-harness-lab/issues) and
[pull requests](https://github.com/mihazs/agent-harness-lab/pulls) before starting.
Read [AGENTS.md](AGENTS.md) for repository boundaries and publication privacy.

## Choose a small task

Start with [good first issues](https://github.com/mihazs/agent-harness-lab/issues?q=is%3Aissue%20is%3Aopen%20label%3A%22good%20first%20issue%22).
These should name the files, desired outcome, tests and scope boundaries. You can
also report a confusing command, improve one explanation or add a synthetic control.
The [scientific roadmap](https://github.com/mihazs/agent-harness-lab/issues/3) includes
advanced isolation and evaluation research; it is not a beginner task list.

Comment on the relevant issue with your intended scope and ask questions there.
For a small correction, a focused PR is fine. Discuss larger changes before investing
in them. Usage questions can use Issues while Discussions is unavailable. No
maintainer response-time guarantee is offered; unassigned work is not a promise of review.

## Local setup

Requires Git and Python 3.11+. A POSIX shell can use:

```sh
git clone https://github.com/mihazs/agent-harness-lab.git
cd agent-harness-lab
git switch -c your-focused-change
python3 -m agent_harness_lab --help
```

No pip, Node, agent credentials or ML dependencies are required for the default
Python test suites. Use a fork if you do not have repository push access. Optional
ML experiments use `.[ml]` in a virtual environment and are outside the default path.
Do not start paid inference merely to validate documentation.

## Pre-commit duplication check

Run `npm ci` followed by `npm run hooks:install` with the latest stable Node.js to
install pinned jscpd and Lefthook development dependencies and enable the
`lefthook.yml` pre-commit job. Automatic npm lifecycle scripts are disabled so
Lefthook's dependency postinstall cannot replace a foreign hook before the guard
runs. Setup migrates this repository's old local `.githooks` setting. It refuses
a foreign `core.hooksPath` or pre-commit handler; integrate the Lefthook job
explicitly if you already manage hooks elsewhere.
No global Git settings or installed AHL runtime dependencies are changed.

`npm run duplication:check` requires **zero clones** in staged Python source.
Any clone blocks the commit, including pre-existing duplication. Refactor shared
logic using DRY rather than adding a baseline or increasing a threshold.
`npm run duplication:report` applies the same zero-clone rule to
duplication in the working tree. Scans cover application code, tools, scripts and
the evaluator runner, excluding tests, fixtures and examples; minimum clone size
is five lines and 50 tokens.

The hook scans regular files copied directly from Git index blobs into a private
temporary directory. It neither stashes edits nor creates Git worktrees, invokes
checkout filters, changes the index, or includes unstaged repairs in its result.
Reports stay local. No clone baseline or duplication allowance is maintained.
Python-only test runs still work without Node; executable hook tests are explicitly
skipped when jscpd or Lefthook is not installed.

## Architecture map

| Area | Files and responsibilities |
| --- | --- |
| CLI and orchestration | `ahl`, `agent_harness_lab/cli.py`, `workflow.py` |
| Evidence and diagnosis | `agent_harness_lab/evidence.py`, `telemetry/`, `tools/diagnose.py`, `tools/observations.py` |
| Task bank and evaluation | `agent_harness_lab/evaluation/`, `skills/harness-evaluation/scripts/` |
| Controlled experiments | `optimization/`, `experiments/`, `diagnostics/` under `agent_harness_lab/` |
| Monitoring and recovery | `agent_harness_lab/monitoring/`, `promotion/` |
| Agent skills and contracts | `skills/harness-evaluation/`, `skills/harness-self-improvement/` |
| Tests and public inputs | `tools/tests/`, evaluator `test_*.py`, `examples/`, evaluation `fixtures/` |
| Distribution | `install.py`, `pyproject.toml`, `scripts/`, `.github/workflows/`, `.changeset/` |

The map identifies areas, not permission to modify all of them. Newcomers should
follow the relevant issue's narrower paths. [Documentation navigation](docs/README.md)
links implemented/pending contracts and input schemas.

## Validate your change

Run both suites, plus the relevant CLI smoke check:

```sh
python3 -m unittest discover -s skills/harness-evaluation/scripts -p 'test_*.py'
python3 -m unittest discover -s tools/tests -p 'test_*.py'
python3 -m agent_harness_lab --help
git diff --check
```

Report failures and skips separately. The containment probe may be unsupported on
your host; a skip is not isolation certification. For the public diagnosis example:

```sh
python3 -m unittest discover -s tools/tests -p 'test_quickstart.py'
```

Use standard-library Python unless a concrete requirement justifies a dependency.
Add a focused behavioral regression for changed logic. For docs, execute the commands,
check relative links and compare expected output. Preserve skill activation metadata
and authorization boundaries. For substantive skill changes, use the owned evaluation
skills, snapshot baseline/candidate, freeze graders before scoring and report paired
outcomes and limitations. Do not change held-out inputs in response to scores.

## Privacy and synthetic fixtures

Public reproductions must be independently synthetic or authorized public-source
material. Do not publish private project identities, paths, histories, excerpts,
findings or derived aggregates, even after redaction. Keep traces, model caches,
credentials, generated trial workspaces and memory routing out of commits. Inspect
`git diff --cached` before publishing. Ignored local artifacts are not public inputs.
For security reports follow [SECURITY.md](SECURITY.md) instead of public Issues.

## Submit a focused PR

Link the issue, describe observable before/after behavior and list actual executed
checks, environment, failures, skips and untested platforms. Keep unrelated refactors
out. Distinguish an observed pattern from a supported diagnosis or validated repair.
Label a review of your own work as self-review. Correct review findings and rerun
affected checks. Maintainers assess scope, accuracy, compatibility and verification;
CI must be checked at the proposed commit. Never bypass protections or disable tests.

Participation follows [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Contributions are
offered under the repository's MIT license. Maintainers can acknowledge contributors
in PR/release notes without promising response times or outcomes.

## Versions and changelogs

Every completed milestone must ship as a small release before starting the next
milestone. Keep the Changeset, validation and release PR focused so completed work
does not wait for unrelated features. A local commit or green PR is not a release;
verify the published tag, archive, installer and checksums.

Maintainers always use the latest stable Node.js for Changesets and jscpd; CI
resolves `node-version: latest` from the current distribution. Release users do not
need Node or a checkout. Run `npm ci`, `npm run hooks:install`, then `npm run changeset`, and commit the
changeset with each releasable change. Do not manually edit release versions.

The version workflow creates a PR using `npm run version-packages`, which runs
Changesets and synchronizes Python metadata. Merging that PR publishes the release
archive, installer and SHA256SUMS through `npm run release`. Releases include only
tracked application code and skills; never add private examples to those trees.
Run the two README test suites before publication. Review Changesets-generated
notes for privacy just like any other staged content.

## Offline documentation links

Run `python3 scripts/check_docs.py` to check local destinations in tracked `.md`
files. It resolves inline links and images relative to their containing file,
decodes URL-encoded paths, and ignores fragments, query strings, external URLs,
code fences and simple same-line code spans. Missing destinations report the
source file, line and link; the command exits nonzero. Paths outside the repository
are rejected. Markdown sources that cross symlinks are rejected before reading,
so tracked aliases cannot expose untracked or ignored files. Untracked Markdown
and private artifacts are not scanned.

This is a file-existence check, not a general Markdown parser: reference-style
links, HTML, multiline links, escaped delimiters, nested parentheses and heading
anchors are not validated. Use percent encoding or angle brackets for spaces;
encode parentheses in file names. No network access or dependencies are needed.

Focused tests: `python3 -m unittest discover -s tools/tests -p 'test_docs.py'`.
