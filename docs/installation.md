# Installation

[Documentation index](README.md) · [Offline source quickstart](getting-started.md)

## Install from a release

Requires Python 3.11+, a symlink-capable filesystem, and `~/.local/bin` on PATH.
Inspect the installer before executing it (the download still requires trust in the release source):

```sh
curl -fsSL https://github.com/mihazs/agent-harness-lab/releases/latest/download/install.py -o /tmp/ahl-install.py
less /tmp/ahl-install.py
python3 /tmp/ahl-install.py --agent codex
```

The compact alternative executes the downloaded installer directly:

```sh
curl -fsSL https://github.com/mihazs/agent-harness-lab/releases/latest/download/install.py | python3 -
```

The installer downloads the versioned release archive, verifies its SHA-256 checksum,
and installs **the CLI and both skills for Claude Code and Codex**. No Git checkout,
pip environment, or Node.js installation is required. The release installer source
is [install.py](../install.py).

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

## Platform evidence

The source quickstart and both test suites were exercised on Linux with Python
3.12.14 for this onboarding change. The existing CI matrix targets Ubuntu with
Python 3.11–3.13; check the exact commit's Actions results before treating it as passed.
macOS, Windows and WSL were not exercised in this onboarding work. Do not infer
installer support merely from Python availability; symlink and shell behavior matter.

The installer checks archive SHA-256 against the release's checksum file. This
checks integrity relative to that file, not independent publisher authentication.
Installer behavior, ownership protections and checksums are unchanged here.

## Common setup problems

- `ahl: command not found`: check that the installer's selected bin directory is
  on PATH, or use `python3 -m agent_harness_lab` from the checkout.
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
