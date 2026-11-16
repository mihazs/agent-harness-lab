# Live evaluation gate reference

The `Live evaluation gate` workflow is the only place a maintainer starts a paid,
model-running evaluation from CI. Today it validates a requested plan and stops: it
reads no secret, calls no model and uploads nothing. A green run means the plan is
bounded and trusted, never that anything was measured. Executing the plan with scoped
credentials remains open in [#16](https://github.com/mihazs/agent-harness-lab/issues/16).

## Start it

Dispatch it by hand from the default branch:

```sh
gh workflow run live-evaluation.yml --ref main \
  -f runtime=claude -f provenance=synthetic \
  -f max_trials=20 -f budget_usd=5 -f pricing_date=2026-11-01 -f max_minutes=60
```

No other trigger exists. A pull request, a fork, a push or a schedule cannot start it.
A schedule is added only after the maintainers opt in to its cost and data policy.

## Inputs and bounds

| Input | Allowed value | Why |
| --- | --- | --- |
| `runtime` | `claude` or `codex` | One runtime stratum per run; strata are never pooled into one effect |
| `provenance` | `synthetic` or `approved-public` | Private-history runs never feed a CI artifact; redaction does not change that |
| `max_trials` | integer, 1 to 200 | Hard ceiling on counted trials, retries included |
| `budget_usd` | number above 0, at most 100 | Hard spend ceiling; `nan`, `inf` and negatives are refused |
| `pricing_date` | ISO date | Cost stays unknown without a supplied, dated price |
| `max_minutes` | integer, 1 to 360 | Hard wall-clock ceiling |

A missing, empty or malformed input is refused, never defaulted. The gate also refuses
any event other than `workflow_dispatch` and any ref other than `refs/heads/main`.
It prints each reason to standard error and exits 2; it exits 0 only when nothing is
refused.

## Workflow trust policy

`python3 scripts/live_evaluation_gate.py policy` scans `.github/workflows` and exits 2
with one line per finding. The offline suite runs the same scan, so a change that opens
a path to paid work fails before merge.

| Finding | Applies to |
| --- | --- |
| `pull_request_target`, which runs with secrets against untrusted code | Every workflow |
| A secret other than `GITHUB_TOKEN`, or `secrets: inherit` | Every workflow except `live-evaluation.yml` |
| A trigger other than `workflow_dispatch`, or no trigger | `live-evaluation.yml` |
| No `github.ref == 'refs/heads/main'` condition, concurrency group, job timeout, `persist-credentials: false` checkout or `contents: read` permission | `live-evaluation.yml` |
| `cancel-in-progress: true`, which would kill a running billable evaluation | `live-evaluation.yml` |

Comment lines are ignored. The scan is static text matching: it does not evaluate
expressions or reusable workflows, so it supplements review of a workflow change and
does not replace it.

See [Evidence classes](validation.md#evidence-classes) for what a gate result can and
cannot support.
