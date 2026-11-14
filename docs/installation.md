# Installation

[Documentation index](README.md) · [Offline source quickstart](getting-started.md)

## Install from a release

Requires Python 3.11+, `curl`, a POSIX shell and a symlink-capable filesystem.
Install the CLI and the skill for Codex and Claude Code with one command:

```sh
curl -fsSL https://github.com/mihazs/agent-harness-lab/releases/latest/download/install.py | python3 -
```

This executes code from the project's latest release. To inspect first, download
`install.py` to a fresh temporary directory, read it, then run it with Python 3.11+.
No administrator privileges are required. The installer prints the exact launcher
path and a PATH command if needed; it does not edit your shell startup files.

The installer downloads the versioned release archive, verifies its SHA-256 checksum,
and installs **the CLI and the skill for Claude Code and Codex**. No Git checkout,
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

## Keep your global instructions in step

The installer and `ahl update` also refresh one marked region of your global agent
instruction files, in the same step that repoints the skill links, so an agent
started anywhere sees the current short guidance without you editing a file. The
files are `CLAUDE.md` in `$CLAUDE_CONFIG_DIR` (default `~/.claude`) for Claude, and
`AGENTS.md` in `$CODEX_HOME` (default `~/.codex`) for Codex, following `--agent`; the
shared `~/.agents/AGENTS.md` is included only if it already exists. A directory that
does not exist is skipped and never created. Only the text between
`<!-- agent-harness-lab:start ... -->` and `<!-- agent-harness-lab:end -->` is ever
changed; every other byte of your file stays identical. See the
[reference](global-instructions-reference.md) for the markers and
[why it works this way](global-instructions-rationale.md).

1. Run `ahl update`. It prints `Instructions updated: PATH` (or `created`) for each
   file it wrote and where the previous bytes are saved, and nothing for a file that
   was already current. The first update from a release older than this feature
   installs it with the old installer; run `ahl update` once more to write the region.
2. Check the state offline with `ahl doctor`: each target is `current`, `stale`,
   `absent`, `skipped` or `refused`, with the exact fix for a refused file.
3. To stop managing the files, run `ahl update --no-instructions` (or pass
   `--no-instructions` to `install.py`). The choice is recorded and later updates
   respect it; `ahl update --instructions` turns it back on. An existing region is
   left as it is; delete the lines between the markers, markers included, to remove it.
4. If the update exits 3, it finished and the launcher and skill links are complete,
   but a file was refused or not written. Stderr names the file, the typed reason and
   the exact fix, for example malformed markers or a symlink into a file you do not own.
   Apply the fix and run `ahl update` again.
5. To undo an update, run the `ahl rollback install-NAME` command that the update printed.
   It puts back the previous instruction files, launcher, skill links and receipt, but
   only if none of them changed since; otherwise it changes nothing and names the files
   that differ.

If you edit the text inside the markers, the next update replaces it and prints
`region_edited_by_user_replaced`; your edited text stays in the backup. Keep your own
instructions outside the markers.

## Moving from two skills to one

Earlier releases installed two skills, `harness-evaluation` and `harness-self-improvement`.
There is now one: `harness-self-improvement`. Asking an agent to evaluate, fix, improve
or maintain a harness all ends at the same skill, and the evaluation methodology and
runner it used to carry live in that skill's `references/` and `scripts/`. The name
`harness-self-improvement` was kept so existing installs and global links stay valid.
The activation description lists every phrase that triggered either skill, and ordinary
coding and application testing still do not trigger it.

Upgrading needs no manual step. `ahl update`, the release installer and `ahl skills`
back up a `harness-evaluation` link that they created (the backup path is printed) and do not
recreate it. An ordinary directory or a symlink you made yourself with that name is left
untouched, so remove it by hand if you no longer want it. The previous release stays under
`~/.local/share/agent-harness-lab/releases/`, so a link you kept keeps working until you delete
it.

## Check that the installation is current

A fix that is released but not installed has no effect on you. `ahl doctor` reports,
offline, the installed version, where the `ahl` launcher on PATH and each global skill
link resolve, and prints a visible `WARNING` with the fix `ahl update` when the launcher
and the skill links point at different releases or the launcher is older than the
minimum `ahl` version a linked skill declares ("Requires `ahl` X or later"). The JSON
report has an `installation` object and an `installation` check with status `warn`;
a warning never changes the readiness exit code. Source checkouts and pip installs are
reported as `not_checked`, not as a pass.

`ahl update --check` additionally compares the installed version with the latest
published release. It makes one read-only HTTPS metadata request, downloads nothing and
changes nothing; it prints `update available, run: ahl update` or `up to date` and exits
2 if the release metadata cannot be fetched. `ahl doctor` itself never uses the network.

## Platform evidence

The source quickstart and both test suites were exercised on Linux with Python
3.12.14 for this onboarding change. The existing CI matrix targets Ubuntu with
Python 3.11–3.13; check the exact commit's Actions results before treating it as passed.
macOS, Windows and WSL were not exercised in this onboarding work. Do not infer
installer support merely from Python availability; symlink and shell behavior matter.

The installer checks archive SHA-256 against the release's checksum file. This
checks integrity relative to that file, not independent publisher authentication.
Updates reuse the same checksum checks, ownership protections and link rollback.
A failed receipt write also rolls back link changes; a problem with an instruction file
never does, because those are handled after the links are committed. Previous release directories
remain available; updates do not start evaluation or a background process.
Concurrent installs or updates to the same root are refused while its transaction
is active; retry after the other command finishes.

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
