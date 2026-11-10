# Trial surface isolation

Use this reference when a trial result depends on which harness surfaces the
runtime saw. `agent_harness_lab/evaluation/trial_surfaces.py` owns the behavior;
the runner records it in `results.json` under `isolation`.

## What a trial copies

A manifest may name `harness_paths`: safe relative files copied from each version
into its fresh fixture workspace at the same relative path. A path the version
lacks is removed from that workspace, so additions and deletions compare. A path
that crosses a symlink, or a fixture entry at that path that is not a regular
file, is refused. Each trial's `workspace_surfaces` lists the included files with
their surface kind and SHA-256, the absent paths, and `observed_loading:
unknown`. Use the [surface matrix](../../harness-self-improvement/references/surfaces.md)
for what each kind is and whether a candidate may change it.

## What a trial requests

Each request is made only when a `surfaces.CAPABILITIES` row names it; a missing
row means the request is not made.

- Claude Code: `--setting-sources project`, `--strict-mcp-config` with an empty
  `--mcp-config`, and `--settings` with `disableAllHooks`. The two staged JSON
  files sit beside the workspace with mode `0600`.
- Codex: a temporary `CODEX_HOME` holding only the copied `model` and
  `model_reasoning_effort` defaults and one trust entry for the trial workspace.
  It holds no saved login, so a run needs `CODEX_API_KEY`; without it the Codex
  CLI run blocks as `codex_credentials_unavailable`. User skills under
  `HOME/.agents/skills` still load; the runner disables copies that share a name
  with a skill under test.

Neither the real `CODEX_HOME` nor the Claude Code user directory is named in the
trial command.

## What the descriptor claims

The descriptor records requests, never observations (`observed: false`). Its
status is `requested_documented_unobserved` only when every request has a
`verified` capability row. At the current pins (Claude Code 2.1.317, Codex
0.166.0) every request row is `unverified`, so the status is
`inherited_unverified`. No descriptor ever says `isolated`, and
`security_isolation`, `network_isolation` and `full_certification` are always
false.

The descriptor also lists, per runtime, what was requested to be excluded, what
is inherited and what is unknown:

- Claude Code inherits authentication stored outside settings files, project and
  managed permission rules and sandbox settings, managed settings and hooks, and
  allowlisted environment variables (or the full host environment with
  `--inherit-env`). User `CLAUDE.md`, auto memory and user plugins and skills are unknown.
- Codex inherits allowlisted environment variables (or the full host environment
  with `--inherit-env`), system configuration layers and `HOME/.agents/skills`. Hooks without trust entries are unknown.

## Reporting

Report isolation as the descriptor states it. An `inherited_unverified` status
means user and local layers may have influenced the trial; do not describe the
run as isolated or as clean of user configuration. A native run that observes a
request at the pinned version is the only way to move an item from unverified to
observed; until then, record effective settings as unknown. This is not network
isolation or a hostile-agent security boundary.
