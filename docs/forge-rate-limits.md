# Share a forge API quota safely

GitHub meters REST (`core`), GraphQL, Search and other resources separately, and the
quota belongs to the identity, not to AHL: every tool and process using one token
spends the same allowance. AHL's forge readers therefore read the limiter's own
signals, keep a reserve nobody else's work should lose, and halt as blocked access
instead of draining the quota. Nothing here retries a permission denial.

## Run several AHL processes safely

Do nothing special: processes of one user share the private limiter file
`$XDG_STATE_HOME/agent-harness-lab/forge-rate-limits.json` (default
`~/.local/state/agent-harness-lab/`). Each response updates the latest limit, remaining
count and reset time for its (host, resource, identity tag); the next request in any
process backs off from that file. Check the current allowance without spending any of it:

```sh
ahl sources github quota --authorization-env GITHUB_AUTHORIZATION
```

The command calls the free `/rate_limit` endpoint, prints every bucket (limit, used,
remaining, reset) as JSON, and records it in the shared file. It never spends core quota.

## Tune a run

Optional keys of a manifest's `limits` object (GitHub, Gitea, GitLab and Mem0):

| Key | Default | Meaning |
| --- | --- | --- |
| `request_budget` | none | Most quota-charged requests this run may send; a 304 is not charged. |
| `reserve_fraction` | `0.05` | Share of the observed limit never spent (for 5,000 requests, the last 250). |
| `max_wait` | `0` | Longest wait in seconds that is taken instead of halting. |

## Read the receipt

`preview`, `import` and `recheck` of a forge source print `rate_limit` with
`requests_used`, `conditional_hits`, `waits` (count and seconds), per-bucket
`remaining_start` and `remaining_end`, `halt_reason` and `reset`. `remaining_start` is
the shared file's value when one existed, else the first response's. A halted `preview` or `import` exits
with code 3 and `"status": "blocked"`, `"checkpoint_committed": false`; the read is
incomplete, never a checkpoint and never evidence of absence.

## Reference: how a response is classified

- `x-ratelimit-limit`, `-remaining`, `-used`, `-reset`, `-resource` and `retry-after`
  are parsed on every response; invalid values are dropped.
- A 403 or 429 with `remaining` 0 is a **primary** limit. A 403 or 429 with
  `retry-after`, an error body naming a rate limit, or any bare 429 is a **secondary**
  limit. Any other 403 is a permission denial: it is reported as such and never retried.
- A rate limit is retried only for a GET, at most three times, after `retry-after`, the
  time to reset, or an exponential backoff with jitter (base 60 s), and only when that wait
  is at most `max_wait` and fits the collection deadline. Otherwise the read halts with
  `primary_limit` or `secondary_limit` and the reset time. GraphQL POSTs are never retried.
- Before sending, a request halts with `reserve_floor` at the reserve, `request_budget` at the
  per-run budget, or `secondary_limit` during a shared cooldown; a shorter wait is taken
  within `max_wait`. Waits use an injectable clock and can be cancelled.
- The state file is owner-only (`0600`, directory `0700`), updated atomically under a
  lock, holds at most 64 numeric entries, drops entries after their reset, and a corrupt,
  oversized or malformed file is ignored and rebuilt. It never holds a token, header,
  login or URL; the identity tag is a truncated one-way hash.

## Not yet implemented (issue #665)

Routing a read between REST and GraphQL buckets, GraphQL cost charging, resuming paginated
reads from a stored cursor and per-request routing receipts are not implemented. A halted
read is incomplete and restarts from the first page.

## Choose the `gh` command family

`gh pr` and `gh issue` use GraphQL; `gh run`, `gh release` and `gh api repos/...` use REST.
When one bucket is saturated, reading a commit's status through `gh pr view --json
statusCheckRollup` spends GraphQL points instead of REST requests. `scripts/sync_project.py`
shares this limiter: it stops at the GraphQL reserve and reports a rate limit as one.
