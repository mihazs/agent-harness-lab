# Release publication

How the release workflow publishes version commits, and why pending changesets do
not block a release. Maintainer reference, with a verification how-to.

## Explanation: why a version commit could be skipped

`changesets/action` runs its `publish-script` (`task release`) only when no
changeset is pending on `main` at the moment a version commit lands. When several
pull requests merge close together, the version commit nearly always finds another
pending changeset, so the action opens a new version PR instead of publishing and
that version is never tagged or released.

The release workflow therefore has a second, idempotent step after the Changesets
step: `scripts/publish_pending_releases.py`. The Changesets step is unchanged
(it still opens the version PR and creates no GitHub release itself), and the suite
still runs first and stops at the first failure.

## Reference: the publish-on-version-commit rule

On every push to `main` the step lists the GitHub releases, then walks the history
of `package.json` up to the commit being built (`$GITHUB_SHA`) to find each commit
that introduced a version.

- A version is published only if its tag has no GitHub release, it is newer than the
  latest release, and `CHANGELOG.md` has its `## <version>` entry at that commit.
  A feature commit with a stale version never qualifies.
- Missing versions are published oldest first. Each is built in a temporary
  `git worktree` at its own version commit and published with
  `scripts/publish_release.py`, which refuses an existing tag, requires a clean
  committed tree and never publishes the private npm package.
- The step first runs `git reset --hard "$GITHUB_SHA"`, because the Changesets step
  leaves its version edits in the tracked tree. If the checkout still has tracked
  modifications the script refuses before mutating anything. When every version is released it exits 0 and changes nothing.
- A failing publication stops the step; later versions are retried on the next run.
- With no release at all only the current version is published, not all history.

Permissions, the `release-${{ github.ref }}` concurrency group with
`cancel-in-progress: false`, and the suite gate are unchanged. A run cancelled
between the version commit and publication is repaired by the next run, which
publishes every missing version in order.

## How-to: verify the first run after merge

1. Merge this change; wait for the `Version and release` run on `main`.
2. Open the run and check the `Publish unreleased version commits` step. If every
   version is released it prints nothing and succeeds; otherwise it prints
   `v<version> published from <sha>` per release.
3. Run `gh release list --limit 5` and confirm the latest tag equals
   `package.json` on `main` when that version's CHANGELOG entry exists.
4. Re-run the workflow (`workflow_dispatch`); the step must be a no-op.

Run the offline tests with
`python -m pytest tests/integration/packaging/test_publish_pending_releases.py`.

## How-to: verify the installed artifact after publication

`task release:verify COMMIT=SHA` downloads the published archive, checks its checksums
and tag, then installs it with the downloaded `install.py` into a temporary prefix with
a temporary `HOME` and state directory (no network after the download) and runs the
reported scenarios through the installed `ahl` launcher with stub `claude`/`codex`
executables on PATH: zero-config runtime selection with and without a host marker,
`--history` read-only toward a synthetic session store, the size-capped analysis brief and
both honest no-change dispositions, the state-home override and `ahl doctor`'s installation
report. Reference: the scenarios live in `tests/integration/packaging/installed_scenarios.py`
and are shared with the build-from-checkout test, so a reverted fix fails both.

## Reference: regression pin markers

A pin test file declares `# fixed-in: COMMIT [-k EXPRESSION]` on its own comment line,
one per fix. `python scripts/check_pins.py` (`task test:pins`) copies the file into a
temporary worktree at `COMMIT^` and runs it. Verdicts: `non-vacuous` (tests fail there),
`VACUOUS` (passes on the unfixed parent, exit 1) and `INCONCLUSIVE` (cannot run: missing
commit, collection error, no tests selected, timeout; exit 3). Inconclusive is never a pass.
A pin names a commit in the history of `HEAD`: a feature-branch commit resolves only while its branch
exists, so the integration test `test_every_pin_names_a_commit_in_the_history_of_head` fails on one (and
is skipped, never passed, where the history is shallow).
