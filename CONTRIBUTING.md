# Contributing

Open an issue describing the observable problem and a small reproduction, or send
a focused pull request. Check existing issues and pull requests before duplicating
work. Use Python 3.11+ and run both test commands in README.md.

Preserve activation metadata, authorization boundaries and the dependency-free
core. Add focused behavioral tests for new logic. For skill behavior changes,
retain baseline/candidate snapshots, freeze graders before scoring, and report
paired outcomes and limitations. Never commit private history or trial workspaces.

Explain what changed, why, and the checks performed. Contributions are offered
under the repository's MIT license. Maintainers review correctness, scope and
verification before merging; no response-time guarantee is offered.

## Versions and changelogs

Maintainers use Node.js 22+ for Changesets; users of GitHub release bundles do not
need Node or a checkout. Run `npm ci` then `npm run changeset`, and commit the
changeset with each releasable change. Do not manually edit release versions.

The version workflow creates a PR using `npm run version-packages`, which runs
Changesets and synchronizes Python metadata. Merging that PR publishes the release
archive, installer and SHA256SUMS through `npm run release`. Releases include only
tracked application code and skills; never add private examples to those trees.
Run the two README test suites before publication. Review Changesets-generated
notes for privacy just like any other staged content.
