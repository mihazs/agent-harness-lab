# Private memory store

[Command overview](command-reference.md) · [Evidence reuse contract matrix](memory-reuse-contract-matrix.md) · [Why reuse is split by authority](memory-reuse-rationale.md) · [Stage reuse planner](stage-reuse-planner.md)

`ahl memory` manages an opt-in, local evidence store. Immutable, hash-addressed
objects are authoritative; a SQLite index is derived from them and can always be
rebuilt. This slice provides the store, its integrity checks and its maintenance
commands. Nothing populates it yet and no planner reads it. The store never modifies a
`HistoryStore`: a row here is never proof that an original store was updated. The separate
`history` command only reads selected stores.

## Create and check a store

Choose a directory outside every checkout, then initialize it. There is no default
location; pass `--store` or set `AHL_MEMORY_STORE`. Output is JSON on stdout.

```sh
ahl memory init   --store /abs/path/ahl-memory --exclude /abs/path/project --exclude /abs/path/bank
ahl memory status --store /abs/path/ahl-memory
ahl memory verify --store /abs/path/ahl-memory
```

Repeat `--exclude` for every directory the store must never overlap: the project, task
bank, source authority, trial workspaces, runtime image, credentials and any existing
protected controller store. `init` refuses a path that is relative, contains `..`, crosses
a symlink, sits inside a Git checkout or an existing controller store (any directory
that holds a `.store.json` marker, such as an evaluation history), overlaps an excluded
directory (compared after resolving the excluded path) or is not a new or empty owner-only directory.
The refusal also runs the other way: a controlled `ahl improve` evaluation store that overlaps the
directory named by `AHL_MEMORY_STORE` is refused before any read, so the two stores cannot nest.

| Command | Effect |
| --- | --- |
| `init` | Creates the store; refuses a non-empty directory |
| `status` | Cheap summary; object files are counted, not re-hashed. An unusable index prints `null` counts, never zero. `fts5` reports whether this SQLite build has FTS5 (`true`, `false`, or `null` when the probe failed); nothing here uses it yet |
| `verify` | Re-hashes every object, checks the index and reconciles the two. Exit 3 when anything is wrong |
| `reindex [--dry-run]` | Rebuilds the index from verified manifests, then swaps it in; the old index stays if the rebuild fails |
| `gc [--dry-run \| --apply]` | Plans removal of unreferenced objects and stale temp files; planning is the default and `--dry-run` says so explicitly; `--apply` deletes exactly that plan |

Every command prints JSON on stdout; `--json` is accepted for uniformity and changes nothing.
Exit codes: 0 completed, 2 invalid input or refused path, 3 blocked (read-only store,
busy writer, unusable index) or `verify` found problems.

## List existing evaluation histories

`ahl memory history` shows runs that already exist in sealed evaluation `HistoryStore`s
without copying any of their authority. It needs no memory store, scans nothing on its own
and writes nothing: you name every store with `--history-store` (absolute path, repeatable).

```sh
ahl memory history --history-store /abs/path/history
```

Each row references the original (`authority_store_ref`, a digest of the store location, and
`original_run_id`) and carries the spec, bank, bank revision, oracle, baseline, candidate,
grader and runtime digests the original recorded. Rows are recomputed on every call by the
HistoryStore's own validators, so changes in authority show up at the next read:

| `current_validity` | Meaning | `comparison_eligibility` |
| --- | --- | --- |
| `current` | Sealed, oracles active, bank authority still eligible | `eligible` unless provenance is missing |
| `invalidated_by_oracle_quarantine` | A dependent oracle was quarantined; the result is historical evidence only | `not_comparable` |
| `authority_not_current` | Sealed, but the bank authority or sealed evidence no longer validates | `not_comparable` |
| `incomplete` | Not sealed | `not_comparable` |
| `unreadable` | The original run does not validate; nothing else is claimed | `unknown` |

A run lacking bank binding, a harness snapshot, grader provenance or runtime is
`legacy_unverified`, listed with the reasons (`bank_binding_missing`, `snapshot_missing`,
`grader_provenance_missing`, `runtime_missing`) and null fields; nothing is inferred from path
names or timestamps. Isolated sandbox roles (`AHL_SANDBOX_ROLE` set) are refused with exit 3.
Exit 0 listed, 2 invalid input, 3 blocked or busy. The `evaluations` table in the index is
unchanged; incremental writes into the memory store are a later slice.

## Recover from damage

| Symptom | What to run | Result |
| --- | --- | --- |
| Index corrupt, missing or from another store | `reindex` | Rebuilt from manifests; records whose artifact or dependency is missing are skipped and listed |
| Object fails its hash | `verify` | Listed; reads refuse it; its records are not rebuilt. Publishing identical bytes again repairs it |
| `index schema N is newer than supported` | Upgrade AHL | The store opens read-only: `status`, `verify` and reads work; writes, `reindex` and `gc --apply` are refused |
| Another writer holds the lock | Retry | One writer at a time; waits up to 5 seconds |

## Reference

Layout (`snapshots/` and `projections/` are reserved and not created yet):

```text
store.json                      schema, store identity, format version; no secrets
index.sqlite                    derived index (WAL, synchronous=FULL)
objects/sha256/ab/<digest>      immutable, mode 0400, single link
locks/writer.lock               owner-only advisory lock
```

**Durability.** An artifact and its manifest are each written to a temp file in the same
directory, fsynced, atomically renamed and followed by a parent-directory fsync. Before
anything becomes durable the index transaction is applied and validated; it commits last.
The manifest (a canonical JSON object naming the project, kind, artifact digest, size,
dependencies and index rows) is the commit point. A write killed earlier leaves at most an
unreferenced blob or temp file, which `gc` reports. A write killed after the manifest is
durable but before the index commits is reported by `verify` as an unindexed manifest, and
`reindex` makes the complete record visible. A partial file never becomes a record.

**Reads.** Every read refuses symlinks, hardlinks, special files and directories, enforces
the byte bound and re-hashes the content. Planted links or odd entries are reported by `verify`.

**Schema.** Version 1 has `projects`, `artifacts`, `dependencies`, `snapshots`, `stages`,
`evaluations`, `metrics`, `comparisons`, `findings` and `finding_links`, with enforced enums,
valid-JSON detail objects of at most 4,096 bytes, foreign keys and one scoped index per
common lookup. Migrations are forward-only and append-only. Unknowns stay unknown: a metric
is `measured` with a value or `unknown` without one, a stage is `complete` only with an
output, and an evaluation's `external_state` can only be `unverified`. Retention-lease and
invalidation tables are added when a later slice needs them.

**Bounds** (defaults; each can be lowered per store):

| Bound | Default |
| --- | --- |
| Artifact size | 16 MiB |
| Manifest size / rows per publication | 1 MiB / 1,000 |
| Files in the store | 200,000 (two per artifact) |
| Total artifact bytes | 4 GiB |
| Rows per project, per table (schema triggers) | 10,000 snapshots; 100,000 artifacts, stages, evaluations, comparisons and findings; 500,000 dependencies, metrics and finding links |
| Rows returned by a list | 1,000 |

**Cleanup.** `gc` derives references from the durable manifests, never from the rebuildable
index, so a lost index cannot cause data loss. It refuses `--apply` while `verify` would
report any problem, because a damaged manifest hides what it references. Retention leases
arrive with a later slice.

**Concurrency.** One writer at a time (advisory lock), SQLite WAL, a busy timeout, readers
that run inside one deferred transaction and therefore see a single consistent snapshot, and
an online-backup method for the index. Objects are immutable and can be copied at any time.
`verify` takes no lock, so run it while no publication is in progress.
