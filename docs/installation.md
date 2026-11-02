# Installation

[Documentation index](README.md) · [Offline source quickstart](getting-started.md)

## Install from a release

Requires Python 3.11+, `curl`, a POSIX shell and a symlink-capable filesystem.
Install the CLI and both skills for Codex and Claude Code with one command:

```sh
curl -fsSL https://github.com/mihazs/agent-harness-lab/releases/latest/download/install.py | python3 -
```

This executes code from the project's latest release. To inspect first, download
`install.py` to a fresh temporary directory, read it, then run it with Python 3.11+.
No administrator privileges are required. The installer prints the exact launcher
path and a PATH command if needed; it does not edit your shell startup files.

The installer downloads the versioned release archive, verifies its SHA-256 checksum,
and installs **the CLI and both skills for Claude Code and Codex**. No Git checkout,
pip environment, or Node.js installation is required. The release installer source
is [install.py](../install.py).

```sh
ahl --version
ahl --help
```

To install for just one runtime, append `--agent claude` or `--agent codex` after
`python3 -`. For a pinned release, append `--version 0.2.0`. After installing a release containing the update command, upgrade with:

```sh
ahl update
```

`ahl update --version 0.62.0` selects a specific published version (including a
downgrade). Updates preserve the original bin directory and selected skill directories.
Older installs without `update` or an installation receipt must rerun the one-line
installer once, using their original `--root`, `--bin-dir` and `--agent` options.
Source checkouts and pip installs are not modified by `ahl update`.

 Owned release links are updated transactionally; existing ordinary
skill directories are backed up. Foreign symlinks and unrelated `ahl` executables
are refused rather than overwritten. Versioned application files live under
`~/.local/share/agent-harness-lab/releases/`; the installer prints any backup path.

Already downloaded the assets? Use the release's `install.py` with
`--archive agent-harness-lab-VERSION.tar.gz --checksums SHA256SUMS`.
`--root` and `--bin-dir` select alternative application/launcher locations.

## Platform evidence

The source quickstart and both test suites were exercised on Linux with Python
3.12.14 for this onboarding change. The existing CI matrix targets Ubuntu with
Python 3.11–3.13; check the exact commit's Actions results before treating it as passed.
macOS, Windows and WSL were not exercised in this onboarding work. Do not infer
installer support merely from Python availability; symlink and shell behavior matter.

The installer checks archive SHA-256 against the release's checksum file. This
checks integrity relative to that file, not independent publisher authentication.
Updates reuse the same checksum checks, ownership protections and link rollback.
A failed receipt write also rolls back link changes. Previous release directories
remain available; updates do not start evaluation or a background process.

## Common setup problems

- `ahl: command not found`: check that the installer's selected bin directory is
  on PATH. For the default location, run `export PATH="$HOME/.local/bin:$PATH"`
  in your POSIX shell, or call `~/.local/bin/ahl --help` directly.
- Existing output directory: select a new path; diagnosis refuses overwrite.
- Missing example after release installation: use the source checkout quickstart;
  do not assume a release contains documentation added after its tag.
- Unsupported Python: run `python3 --version`; use Python 3.11 or later.
- Missing agent authentication: the offline diagnosis does not need it. Set up
  your chosen runtime separately before opting into `improve` or `watch`.
- Unknown capture or no findings: missing evidence is not a successful task. Check
  [input formats and evidence fields](telemetry.md) before drawing conclusions.

For checksum failures or foreign symlink refusals, stop and inspect the assets or
ownership. Do not disable validation or delete unrelated skills to force installation.

## Ask your AI agent to install it

Copy this into a terminal-capable coding agent. This requests installation only;
it does not authorize model-backed evaluations or project harness edits.

```text
Install or update Agent Harness Lab from https://github.com/mihazs/agent-harness-lab
for my current user. Read the repository's current docs/installation.md first.
Check my OS, Python version (3.11+), curl and symlink support. Use the official
release installer; download it into a fresh temporary directory and inspect it
before running it. Preserve any existing installation root, bin directory and
selected Codex/Claude skill directories. If a supported release installation is
already present, use ahl update. Otherwise install the CLI and both bundled skills
for Codex and Claude Code. Do not use sudo, overwrite unrelated executables or
skills, disable checksum checks, or change permission/security settings.
Verify the installed launcher with --version and --help, then verify the skill
links. If PATH needs adjustment, give me the exact command for my shell; do not
silently edit shell startup files. Do not start watch, inference, evaluations,
agent login, or target-project changes. Report the installed version, locations,
verification results and any real blocker. Remove only your temporary downloads.
```
