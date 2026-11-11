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
- If the current checkout has tracked modifications the step refuses before
  mutating anything. When every version is released it exits 0 and changes nothing.
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
