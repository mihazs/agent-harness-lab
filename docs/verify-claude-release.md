# Verify a new Claude Code release

Use this when `ahl doctor` or the `ahl improve --history` receipt says your recorded
Claude Code release is newer than the newest verified one, or when captures are
skipped with `capture_version_differs_from_pin`. The goal is to learn, without
reading any message text, whether the verified adapter can read that release.

Releases inside the verified range are read by the verified adapter and counted
as `pin_compatibility: declared_range`. Any other release is read only by the
descriptive, unverified adapter, which keeps no declared event order. The range and
the rules behind it are in [Runtime version policy](telemetry.md#runtime-version-policy-for-pinned-native-profiles).

## Check one capture

Pick any recent session file for the new release. The command reads field names
only. It never prints a value, a path inside the file, message text or an identifier.

```sh
ahl sources claude pin-check --events SESSION.jsonl
```

The verdict is one of:

| Verdict | Meaning | Exit code |
| --- | --- | --- |
| `compatible` | Every entry type is handled or a declared non-event type, and every required key is present. | 0 |
| `compatible_with_unknown_types` | As above, plus entry or content-block types the adapter does not know. They are skipped and counted, never parsed. | 0 |
| `incompatible` | A required key is missing, a record is malformed, or no handled entry exists. Do not extend the range. | 3 |

The report lists entry types with counts and a status (`handled`, `declared_non_event`,
`unknown`), content-block types, keys that are present but never read, any required
keys that are missing, and the recorded releases that fall outside the verified set.

## Add the release to the verified range

1. Write a names-only skeleton: add `--emit-skeleton NEW_FIXTURE.jsonl`. Every value
   in it is an invented placeholder; the file is created with mode `0600` and is never
   overwritten.
2. Read the skeleton and change nothing that carries real content. Keep it minimal.
3. Save it as `tests/fixtures/claude_contract/<version>.jsonl`.
4. In `agent_harness_lab/telemetry/claude_native.py`, extend `VERIFIED_RANGES`. A
   release is accepted only when its fixture exists, and the range test refuses a
   release that has none. Ranges stay within one minor line.
5. Run the focused tests (`tests/unit/telemetry/test_claude_verified_range.py`,
   `tests/integration/telemetry/test_claude_pin_check.py`) and both README suites.

Never commit a real session file or a skeleton you have not reviewed.

## Read the surfaces that report the lag

- `ahl doctor` adds one `claude_runtime_lag` warning when the session headers of
  the project record a release newer than the newest verified one. It reads
  dotted version strings only, from at most the first 200 session files, under every
  profile including `offline`. The check is never required.
- The `ahl improve --history` receipt carries `history.runtime_lag` once, with counts
  per version and this same next step.
- The analysis brief lists `adapters`: the adapter that produced each capture, and
  how many cases lack a declared event order and how many tool events lack a tool name
  because the descriptive adapter read them. The `verified_adapter` entry under
  `missing_evidence` names the command above.
