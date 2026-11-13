# Try AHL on a real project without changing it

Use this when you want to run `ahl` (or any command that edits harness files) against a real repository and check that the
repository is left as it was (within the limits below). The check runs your command in a temporary detached worktree,
removes the worktree, and compares a fingerprint of the real checkout before and after.

```sh
task test:isolated REPO=/path/to/project -- --ref HEAD --timeout 900 --memory-cap-gib 5 --inherit-home \
  --receipt /tmp/receipt.json -- python -m agent_harness_lab improve '{worktree}' --agent claude --timeout 600
```

Everything before the final `--` configures the check; everything after it is the command. `{worktree}` is replaced by the
temporary worktree path, which is also the command's working directory.

## What it guarantees

- The worktree is checked out from `--ref` (default `HEAD`) with git hooks disabled, so no project script runs. Uncommitted
  edits and untracked files in the real checkout are neither copied nor touched.
- The command gets a private temporary `AHL_STATE_HOME` and, unless you pass `--inherit-home`, a private empty `HOME`. Use
  `--pass-env NAME` for other host variables a runtime login needs. `--log-dir DIR` keeps the command's stdout and stderr in a private folder; the receipt itself keeps only their hashes.
- The worktree is removed and pruned even when the command fails or times out.
- The real checkout is fingerprinted before and after: HEAD, all refs, stash count, linked worktrees, working-tree status,
  the index, the content of tracked changes (`git diff HEAD`), the content of every untracked file, the size and modification
  time of every ignored file, and the shared git config (found through `git rev-parse --git-common-dir`, so it also works when
  REPO is a linked worktree). Any difference is `drift` and the exit status is 1; otherwise the command's own status is
  returned (124 on timeout). Setup problems return 2.

## What it does not undo

The fingerprint hashes content for tracked changes and untracked files. For ignored files (build output, caches) it compares
only size and modification time, because they can be huge: a rewrite that keeps both, or that restores the old modification
time, goes unseen. Other files under `.git` (hooks, `info/exclude`, objects) are not fingerprinted either. A fingerprint
cannot prove more than it reads, so an `unchanged` verdict means none of the listed things differ. Any other process that
writes to the checkout while the check runs (an editor, a dev server) also shows up as drift.

Things outside the repository are not covered: a runtime may write its own session logs under your home directory, and a memory
tool may record the session. Check those separately.

## Reproducibility

The receipt records the resolved commit, the exact command, the memory cap and the verdict, so a run can be repeated with the
same inputs; `--ref` pins the commit, so a moving branch cannot change it. A model run is not deterministic: only the isolation
verdict and the setup are. The receipt names changed files and holds hashes but no file content. It records the command line
verbatim, so never put a secret on it, and keep receipts private because they describe a real project.

`--memory-cap-gib N` runs the command in a `systemd-run --user --scope` unit with that `MemoryMax` and no swap; where
`systemd-run` is missing the cap is skipped. The tests for this tool (`tests/integration/tools/test_isolated_project_check.py`)
use synthetic repositories with pinned dates and need no network, model or home directory.
