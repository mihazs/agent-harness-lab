# Harness Improver

This repository owns the project-agnostic `harness-evaluation` and
`harness-self-improvement` skills under `skills/`. Global skill directories link
to these sources. Preserve their activation metadata and authorization boundaries.

Keep the skill entrypoints compact; put conditional methodology in references
and repeated deterministic operations in scripts. Count independent mechanisms
and observed outcomes, not edits or data volume. Do not import project-specific
service names, paths, policies or credentials into shared guidance.

Use standard-library Python unless a concrete requirement justifies a dependency.
Run the behavioral tests documented in README.md after changes. Keep evaluation
artifacts and raw traces outside skill discovery; `artifacts/` is ignored.
Never commit credentials, raw private session bodies, or generated trial workspaces.

For substantive changes, snapshot baseline/candidate and validate using the owned
skills. Do not alter graders or held-out inputs in response to candidate results.

Before planning a fix, check open pull requests for the problem and touched files.
Preserve the standard-library default; ML runtimes belong in optional extras.
Keep observed patterns, supported diagnoses and validated improvements distinct.
Unknown capture or lineage must not become a success, failure or independence label.
Run both README test suites and CLI smoke checks relevant to a change. Report
failed, skipped and inconclusive checks separately. Never publish private traces,
evaluation workspaces, model caches, credentials or project-specific history.

## Publication privacy

Never commit or publish information about projects analyzed by this tool or
information retrieved from ai-memory. This includes project identities, session
identifiers, paths, excerpts, findings, aggregate statistics, and anonymized reports
derived from private histories. Keep memory routing configuration local and ignored.
Public examples and validation reports must use public research or reproducible
synthetic fixtures only. Inspect staged content before every publication; ignored
local artifacts do not authorize copying their contents into documentation.
