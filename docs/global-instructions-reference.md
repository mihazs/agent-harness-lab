# Global instruction region reference

[Documentation index](README.md) · How to: [keep your global instructions in step](installation.md#keep-your-global-instructions-in-step) · Why: [the managed region](global-instructions-rationale.md)

The installer and `ahl update` keep one marked region of your global agent
instruction files in step with the installed release. This page lists the exact
markers, targets, states, typed reasons, exit statuses and records. Nothing here is
inferred from any particular machine; every value is fixed by `install.py`.

## Markers

```text
<!-- agent-harness-lab:start version=VERSION sha256=DIGEST -->
...managed text...
<!-- agent-harness-lab:end -->
```

- Each marker is a whole line. `VERSION` is the release version; `DIGEST` is the
  SHA-256 of the managed text (the lines between the markers, joined with `\n`, ending
  with one `\n`, with the version substituted).
- AHL owns the bytes from the start of the first marker to the end of the last one.
  It never reads, reorders, re-encodes or re-wraps any byte outside them.
- Exactly one region per file. Another tool's differently named region is left alone,
  including when it sits next to or around ours.

## Managed text

The release ships it as `global-instructions.md` (about 0.5 KiB, at most 2 KiB is
accepted). It states only: use the `harness-self-improvement` skill, which runs
`ahl improve`, to evaluate, fix or improve an agent harness; the default scope is the
current project's harness and global or combined scope needs an explicit request;
never hand-edit global instruction files for this; `ahl update --check` shows
staleness. It contains no paths, project names or private data.

## Targets

| Agent selection | File | When it is a target |
| --- | --- | --- |
| `claude`, `both` | `CLAUDE.md` in `$CLAUDE_CONFIG_DIR`, default `~/.claude` | The directory exists |
| `codex`, `both` | `AGENTS.md` in `$CODEX_HOME`, default `~/.codex` | The directory exists |
| `codex`, `both` | `~/.agents/AGENTS.md` | The file already exists |

The selection is the installer's `--agent`, recorded in `installation.json`, so an
update keeps it. A receipt written before this feature has none recorded; the
selection is then taken from the global skill directories it lists. A directory that
does not exist is never created, even though linking the skills may create it later:
targets are decided before any link is made. When two targets resolve to the same file
it is written once. If the directory exists but the file does not, the file is created
with mode `0600` and only the region.

## Write rules

| Rule | Behavior |
| --- | --- |
| No change | A region that already equals the release's text writes nothing and makes no backup. |
| Backup | Before any change to an existing file its previous bytes are saved under `backups/install-NAME/instruction-N` in the installation root, mode `0600`. |
| Atomic | Temporary file in the same directory, `fsync`, rename; the file's mode is kept and never widened, its group is kept when possible. |
| Line endings | A file that uses CRLF gets a CRLF region; otherwise LF. |
| Appending | The region goes after the last byte. The one byte AHL may add outside the markers is a single line break when the file did not end with one. |
| Concurrent edit | If the file changed after it was read, nothing is written and the file is reported `failed`. |
| Symlink | Followed only when it resolves to a regular file owned by you that is not world-writable; the report names the resolved file. Anything else is refused. |
| Malformed markers | A start without an end, an end without a start, a duplicate, nested or reversed pair, or a marker that is not a whole line is refused and the file is left untouched. |
| Hand-edited region | A region whose text no longer matches the digest in its start marker is replaced, after the backup, with the note `region_edited_by_user_replaced`. A start marker without a digest cannot vouch for its text and is treated the same way. |

## States

`ahl doctor` (offline) and the installer report one of these per target:

| State | Meaning |
| --- | --- |
| `current` | The region matches this release. |
| `stale` | The region exists but differs from this release; `ahl update` replaces it. |
| `absent` | The target exists (or may be created) and has no region; `ahl update` adds it. |
| `skipped` | Left alone: `instructions_opted_out`, `config_directory_missing`, or `instruction_template_missing` for a release that ships no template. |
| `refused` | Not safe to change; the row carries a typed reason and the exact fix. |
| `created`, `updated` | The installer wrote the file or replaced the region. |
| `failed` | A write error (`instruction_write_failed`); the row carries the error and the fix. |

## Typed reasons

Every reason is also a row of the [shared exit-reason table](analysis.md#exit-reasons-and-next-actions),
which `exit_guidance.py` renders; a test keeps that table equal to the installer's.

| Reason | Kind | Cause |
| --- | --- | --- |
| `instruction_markers_malformed` | refused | The markers are not exactly one well-formed pair |
| `instruction_symlink_refused` | refused | A symlink to a missing, non-regular, foreign-owned or world-writable target |
| `instruction_target_unsafe` | refused | A target that is not a regular file you own, or is over 8 MiB |
| `instruction_template_invalid` | refused | The release template is unusable |
| `instruction_template_missing` | skipped | The release ships no template (an older release) |
| `instruction_write_failed` | failed | The write, backup or rename raised an error |
| `region_edited_by_user_replaced` | note | A hand-edited region was replaced; its text is in the backup |

## Exit status

| Command | 0 | 3 | 2 |
| --- | --- | --- | --- |
| `install.py`, `ahl update` | Installed or updated; every selected target is current, written or skipped | Installed or updated, but at least one target is `refused` or `failed`; stderr names each file and the exact fix | Invalid arguments, download or installation error; nothing half-applied |

Instruction files are handled after the launcher, skill links and receipt are
committed, so an instruction problem never leaves them half-updated.

## Options and records

- `install.py` and `ahl update` accept `--instructions` and `--no-instructions`; the
  choice is stored and applies to later updates until the other flag is given.
- `installation.json` keeps `"instructions": {"enabled": true|false, "agent": "claude"|"codex"|"both"}`.
  `schema_version` stays 1; older readers ignore the key.
- Each backup directory that holds a change has a private `manifest.json` listing the
  links, instruction files and receipt it changed, with the SHA-256 of what was written.
  `ahl rollback install-NAME` restores them only if none has changed since, then renames
  the manifest to `manifest.restored.json`; otherwise it changes nothing and lists the
  paths that differ. Exit 0 restored; 2 refused.
- `ahl doctor --json` has `installation.instructions`, one row per target with `agent`,
  `path`, `state`, `reason`, `resolved` and `fix`, and a warning for each `stale`,
  `absent` or `refused` row. A warning never changes the readiness exit status.
