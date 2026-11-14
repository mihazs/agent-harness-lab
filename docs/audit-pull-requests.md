# Annotate pull requests with the static audit

This guide adds the read-only [static audit](static-audit.md) to a repository's
pull requests. Findings appear as check annotations on the changed harness files.

Add `.github/workflows/harness-audit.yml`, replacing each `<sha>` with the full
commit SHA you have reviewed. Add `with: {runtime: claude-code}` to the second step
for Claude Code.

```yaml
on: pull_request
permissions: {contents: read}
jobs:
  audit: {runs-on: ubuntu-24.04, steps: [{uses: "actions/checkout@<sha>", with: {fetch-depth: 2}}, {uses: "mihazs/agent-harness-lab@<sha>"}]}
```

The `runtime` input is `codex` (default) or `claude-code`. The runner needs Python 3.11 or
later, which hosted Ubuntu images provide. The Action uses no third-party action
and no secrets, and runs from a fork's pull request with an empty secret set.

## What it does

The Action runs `ahl audit --json` on the checkout and prints one
`::warning file=...` line per `weak` or `missing` finding (`::notice` for other
statuses). With `fetch-depth: 2` on a pull request it keeps findings for files the
pull request changed, plus findings that are not tied to one file; without that
history it reports every finding. At most 50 annotations are printed. If the audit
does not complete, one `::error` line is printed and the job fails.

## Limits and non-claims

- Use the `pull_request` trigger only. `pull_request_target` and reuse of
  `workflow_run` artifacts would run pull-request-controlled files with more
  authority, and the Action's tests reject both.
- Annotations are convention checks, not diagnoses. They do not say that a
  harness behaves worse or that a change will help.
- The audit makes no model call, executes no hook or project script, and writes
  nothing to the checkout. Its private report goes to a temporary directory that is
  removed afterwards. Nothing is uploaded.
- File names and finding text are untrusted: control characters are dropped, and
  `%`, line breaks, `:` in properties and `::` sequences are escaped.
- Publishing or tagging the Action is a separate maintainer step; pin the SHA you
  have reviewed.
