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

Install [Task](https://taskfile.dev/docs/installation/) and use `task setup` with the latest stable Node.js to
install pinned jscpd and Lefthook development dependencies and enable the
`lefthook.yml` pre-commit job. Automatic npm lifecycle scripts are disabled so
Lefthook's dependency postinstall cannot replace a foreign hook before the guard
runs. Setup migrates this repository's old local `.githooks` setting. It refuses
a foreign `core.hooksPath`, pre-commit or commit-msg handler; integrate the Lefthook jobs
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
skipped when Task, jscpd or Lefthook is not installed.

## Conventional Commits

Every commit subject, pull request title and merge commit follows
[Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/):
`type(scope)!: description`. Types are `feat`, `fix`, `docs`, `style`, `refactor`,
`perf`, `test`, `build`, `ci`, `chore` and `revert`. The scope is optional and uses
lowercase letters, digits and `. _ / -`; `!` marks a breaking change. The description
is required, does not end with a period, and the subject is at most 100 characters.
Git `fixup! `, `squash! ` and `amend! ` prefixes are accepted before a conventional
subject. Default `Merge pull request …`, `Merge branch …` and `Revert "…"` subjects
are rejected; write, for example, `chore: merge pull request #123 from owner/topic`.

`task setup` installs a Lefthook `commit-msg` job beside the pre-commit job, with
the same refusal to replace a foreign, linked, non-regular or oversized hook. If you
manage hooks elsewhere, call `python3 scripts/check_commit_message.py "$1"` from your
own `commit-msg` hook. Check a whole branch with `task commits:check
RANGE=origin/main..HEAD`, or one subject with `python3 scripts/check_commit_message.py
--subject "feat: add a flag"`. Exit status is 0 when conventional, 1 for a violation
and 2 for a usage or Git error.

For pull requests, the required `Tests` check validates the title and every commit
between the base branch and the PR head. GitHub creates the merge commit after that
check, so maintainers type an explicit conventional merge or squash subject.

## Native tests and reusable commands

Application tests live in the `tests/` package: `tests/unit/<area>/` and
`tests/integration/<area>/`, where the area mirrors the code under test, with shared
helpers in `tests/support/`. The [layout rule](.agents/skills/pytest-patterns/SKILL.md#repository-adaptation)
says which tree a test belongs to. Evaluator tests live beside the owned evaluator in
`skills/harness-evaluation/scripts/test_*.py`. Add discoverable cases to these existing
native suites. Put reusable commands in
`Taskfile.yml`; do not create standalone smoke-test scripts under artifacts or
other helper folders.

A test that bounds memory with `tracemalloc` should measure in a fresh interpreter through
`tests/support/fresh_peak.py` rather than inside the pytest process: `tracemalloc` counts
every thread, so another test's child process (three 128 KiB I/O buffers on Python 3.14) or
a first-use cache can decide the result in one layout and not another. Keep the documented
bound as it is and add a deliberate regression the bound must catch.

`task setup:test` installs pinned pytest/xdist into the selected virtual environment.
Taskfile test tasks run `python -m pytest`. Filter variables such as `KERNEL_TEST_FILTER`
take one pytest `-k` expression, and pattern variables such as `REPEATED_TEST_PATTERN`
are file-name globs. A few files must stay runnable under `python -m unittest`; the
[pytest-patterns adaptation](.agents/skills/pytest-patterns/SKILL.md#repository-adaptation) lists them.
`task test` collects both suites once and schedules tests across physical CPU cores.
Set `TEST_WORKERS` to a positive count, or `0` to debug without workers.
The `worksteal` scheduler balances independent tests without a file-by-file runner.
See [pytest integration guidance](https://docs.pytest.org/en/stable/explanation/goodpractices.html)
and [xdist scheduling](https://pytest-xdist.readthedocs.io/en/latest/distribution.html).
CI uses Python 3.14; this is the validation version, not a change to the package's
Python 3.11 minimum.
`task test` runs both suites. `task test:sources` focuses source contracts and the
checkpoint journal; `task test:hooks` runs the real Lefthook integration cases.
CI uses the same tasks. `task build` builds wheel/archive artifacts with existing
build tools; `task venv` and `task setup:build PYTHON=.venv/bin/python` prepare a
local build environment. Select that interpreter with `task build
PYTHON=.venv/bin/python` when the system interpreter has no build tools.

`task install:wheel test:wheel` runs native acceptance and source tests against a
clean wheel installation. `task test:archive` runs them against the extracted
archive. The generic `test:installed` task accepts absolute `PYTHON`, `LAYOUT_DIR`
and `EXPECTED_PACKAGE_ROOT` values for other installed environments. These tasks remove
`PYTHONPATH` so source-tree imports cannot mask missing installed modules. Tests
own their temporary fixtures and require no model accounts or inference packages.
`task release:verify COMMIT=SHA` explicitly downloads this project's current public
release and runs native checksum, archive-version and GitHub tag/target tests.
It then installs the downloaded archive with its bundled installer into an isolated
temporary prefix and home and runs the reported-failure scenarios against the
installed launcher (`tests/integration/packaging/test_installed_artifact.py`); any
failing scenario fails the verification. The same scenarios run against an archive
built from your checkout during `task test`. These publication cases are skipped
during ordinary offline tests.

The shared autouse fixture in `tests/conftest.py` gives every test its own `XDG_*` and `HOME`
directories and removes (for the whole session, so module-scoped fixtures are covered) the caller's `AHL_STATE_HOME`, `AHL_MEMORY_STORE` and
`AHL_MANAGED_PREPARATION`, so an exported override neither changes a result nor receives a write.
A test that needs one sets it itself. A guard test fails when product code reads another `AHL_*`
variable that the fixture does not neutralize; add the name to `AHL_AMBIENT_VARIABLES`.

A regression test written for a reported failure carries a `# fixed-in: COMMIT [-k EXPRESSION]`
comment. `task test:pins` (also run by the release workflow, not the fast path) runs
each such file against the parent of that commit in a temporary Git worktree and fails
when the pin passes there. `task test:runtime-contracts` is an opt-in probe of the real
`claude` and `codex` CLIs (`--version` and `--help` only, no model call); it is skipped
by default and a missing runtime is reported as skipped.

## Architecture map

| Area | Files and responsibilities |
| --- | --- |
| CLI and orchestration | `ahl`, `agent_harness_lab/cli.py`, `workflow.py` |
| Evidence and diagnosis | `agent_harness_lab/evidence.py`, `telemetry/`, `tools/diagnose.py`, `tools/observations.py` |
| Task bank and evaluation | `agent_harness_lab/evaluation/`, `skills/harness-evaluation/scripts/` |
| Controlled experiments | `optimization/`, `experiments/`, `diagnostics/` under `agent_harness_lab/` |
| Monitoring and recovery | `agent_harness_lab/monitoring/`, `promotion/` |
| Agent skills and contracts | `skills/harness-evaluation/`, `skills/harness-self-improvement/` |
| Tests and public inputs | `tests/`, evaluator `test_*.py`, `examples/`, evaluation `fixtures/` |
| Distribution | `install.py`, `pyproject.toml`, `scripts/`, `.github/workflows/`, `.changeset/` |

The map identifies areas, not permission to modify all of them. Newcomers should
follow the relevant issue's narrower paths. [Documentation navigation](docs/README.md)
links implemented/pending contracts and input schemas.

## Validate your change

Run both suites, plus the relevant CLI smoke check:

```sh
task setup:test PYTHON=.venv/bin/python
task test PYTHON=.venv/bin/python
python3 -m agent_harness_lab --help
git diff --check
```

Report failures and skips separately. The containment probe may be unsupported on
your host; a skip is not isolation certification. For the public diagnosis example:

```sh
.venv/bin/python -m pytest tests/integration/tools/test_quickstart.py
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

Use a [Conventional Commits](#conventional-commits) title and commit subjects.
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
need Node or a checkout. Run `task setup`, then `task changeset`, and commit the
changeset with each releasable change. Do not manually edit release versions.

The version workflow creates a PR using `npm run version-packages`, which runs
Changesets and synchronizes Python metadata. Merging that PR publishes the release
archive, installer and SHA256SUMS through `npm run release`, even when other
changesets are pending (see [release publication](docs/release-publication.md)).
Releases include only
tracked application code and skills; never add private examples to those trees.
Run the two README test suites before publication. Review Changesets-generated
notes for privacy just like any other staged content.

## Offline documentation links

Run `task docs:check` to check local destinations in tracked `.md`
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

Focused tests: `.venv/bin/python -m pytest tests/integration/docs/test_docs.py`.
