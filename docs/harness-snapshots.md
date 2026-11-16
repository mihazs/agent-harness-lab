# Identify a harness and fix a baseline

[Private memory store](memory-store.md) · [Command overview](command-reference.md) · [Why a snapshot is not a commit](memory-reuse-rationale.md#a-harness-has-an-identity-and-a-history)

Every evaluation should say exactly which harness it measured and which reference it
compares against. `ahl memory snapshot` names the declared harness surfaces of a
checkout; `ahl memory diff` compares two snapshots. Both are offline, read-only, need no
memory store and print path-free JSON. The record types below (events, change sets and
baseline policies) are library documents that later slices write into the memory store.

## Name the harness of a checkout

```sh
ahl memory snapshot --checkout /abs/path/project --runtime claude-code --version 2.1.317 > before.json
```

The command inventories the checkout with the same read-only walk as
[`ahl sources inventory`](source-contracts.md) and prints one document. The same bytes,
modes, declared runtime and declared trust give the same `snapshot_id` in any directory
and on any day. A change to a harness surface file, its mode, a refused entry such as a
symlink, the runtime or the declared trust gives a new one. A file that is not a harness
surface for the runtime never changes it. Exit 0 prints an identity; exit 3 prints
`"snapshot_id": null` when the declared version is unsupported or an inventory bound was
exceeded; exit 2 is invalid input.

Compare it with a later snapshot:

```sh
ahl memory snapshot --checkout /abs/path/project --runtime claude-code --version 2.1.317 > after.json
ahl memory diff --from-snapshot before.json --to-snapshot after.json
```

The diff lists each added, removed or modified surface by its path hash and the fields
that changed (`kind`, `scope_sha256`, `declared_state`, `content_sha256`, `mode`,
`trust`), plus `context_changed` for the runtime, version, task directory, external
roots or refused entries. It always says `"cause": "not_established"`: a diff shows what
differs, never why a result moved. A snapshot file that was edited after capture, is not
JSON, has duplicate keys or is a symlink is refused with exit 2.

## What a snapshot does not say

- **A commit is not a snapshot.** The `commit` block of a document built with a Git
  `HarnessSnapshot` relates the selected files to HEAD: `files_match_commit`,
  `uncommitted_changes`, or `unknown` when the inventory was captured apart from that
  snapshot or has a file outside the selection. It is an annotation, not part of the id,
  and it always carries `ignored_state: not_captured`, so two snapshots of one commit
  differ whenever the working tree does. The command line prints `relation: unbound`.
- **A declared surface is not a loaded one.** `effective` and `observed_loading` stay
  `unknown`. Mode bits are recorded exactly, so a different umask is a different snapshot.
- **Nothing is published.** `publication_allowed` is `false`; paths and scope
  directories appear only as SHA-256 values.

## Declare the baseline before outcomes exist

A `BaselinePolicy` says how the reference for one claim (its `subject`) is chosen:

| Policy | Reference | `claim_basis` |
| --- | --- | --- |
| `predeclared_fixed_reference` | One named snapshot id, declared before any outcome of the subject is recorded | `inferential_eligible` |
| `previous_valid` | The latest valid completed evaluation of the subject before an instant | `descriptive` |
| `rolling_window` | The last N (2 to 100) valid completed evaluations | `descriptive` |

`declare_baseline` refuses a fixed reference when the recorded events already hold an
`evaluation_completed` or `regression_retested` outcome of the subject at or before the
declaration instant (`BaselineRefused`). `inferential_eligible` only permits a later
inferential claim; it is not one. `check_policy` rebuilds a document from its own
declaration, so an edited `claim_basis` or baseline fails.

`reselect` replaces a fixed reference and returns two documents: a new policy whose
`supersedes` names the old one and whose `claim_basis` is always `descriptive`, and a
`baseline_reselected` event. The original policy and its claim are untouched, so
reselection cannot upgrade a claim. A new inferential reference needs a fresh
declaration, which is refused once outcomes exist. `resolve` turns a policy and the
events into baseline snapshot ids; an evaluation invalidated at any time is skipped, an
evaluation with an unknown snapshot is counted in `skipped_unknown_snapshot`, and no
valid evaluation gives `status: unknown`, never an empty success.

## Reference: events and change sets

Event kinds and their closed fields (`from_snapshot` and `snapshot` may be `null` for
unknown; every other field is a label):

| Kind | Fields |
| --- | --- |
| `harness_changed` | `from_snapshot`, `to_snapshot` |
| `evaluation_completed`, `regression_retested` | `evaluation`, `snapshot` |
| `evaluation_invalidated` | `evaluation`, `reason` |
| `source_gap` | `reason` |
| `grader_quarantined` | `grader` |
| `baseline_reselected` | `from_snapshot`, `to_snapshot`, `policy` |

Each event also has `at` (an ISO-8601 instant with a UTC offset, stored as fixed-width
UTC), `origin` (`controller_observed`, `caller_declared` or `imported_record`), a
`pointer` (`type` of `history_run`, `memory_artifact`, `snapshot` or `source_record`
plus a `ref` label) and, for outcome, invalidation and reselection events, a `subject`.
No kind has a field for a cause, and every event says `causality: not_established`.
Events that point at an evaluation point at the owning `HistoryStore` run by reference;
the pointer is never proof that the original was updated.

A change set (`change_set(name, old, new)`) associates a logical change set label with
two different snapshots and the surfaces between them. Its label names a change the
caller owns, such as a saved change plan; the association is declared, not causal.

`publish` stores a checked snapshot as a `capture` artifact and an event, policy or
change set as a `manifest` artifact in the [memory store](memory-store.md); `timeline`
lists a project's events in time order and refuses a listing longer than one page
instead of truncating it, because a partial timeline cannot show that no outcome came
first. Use a dedicated project id for the timeline.
