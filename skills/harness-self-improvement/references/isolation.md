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
unknown`. Use the [surface matrix](surfaces.md)
for what each kind is and whether a candidate may change it.

## What a trial requests

Each request is made only when a `surfaces.CAPABILITIES` row names it; a missing
row means the request is not made.

- Claude Code: `--setting-sources project`, `--strict-mcp-config` with an empty
  `--mcp-config`, and `--settings` with `disableAllHooks`. The same settings file
  requests `permissions.defaultMode: acceptEdits` and the runtime's command sandbox
  with no unsandboxed fallback, the counterpart of Codex's `workspace-write`: without
  it a `--print` trial is denied every edit and command. The two staged JSON
  files sit beside the workspace with mode `0600`.
- Codex: a temporary `CODEX_HOME` holding only the copied `model` and
  `model_reasoning_effort` defaults and one trust entry for the trial workspace.
  It also gets a `0600` copy of the saved subscription login (`auth.json` under the
  real `CODEX_HOME`), present only while the session runs; see
  [Subscription login](#subscription-login). User skills under
  `HOME/.agents/skills` still load; the runner disables copies that share a name
  with a skill under test.

Neither the real `CODEX_HOME` nor the Claude Code user directory is named in the
trial command.

## Preparation sessions are always pure

The sessions AHL starts itself (the proposer and the evaluation author) get the same
exclusions as trials, whatever the options:

- Claude Code: `--setting-sources project`, `--strict-mcp-config` with an empty
  `--mcp-config`, and `--settings` with `disableAllHooks`, the write grant above and
  `Read` allow rules for the run's output directory and the project (read access
  only), all staged beside the run in `preparation-N.session/` and
  `evaluation-construction-N.session/`. `--plugin-dir` names only AHL's own plugin,
  which carries the skill the prompt loads. Never `--bare`: it disables the
  subscription login and requires `ANTHROPIC_API_KEY`.
- Codex: `--ignore-user-config` and `--ignore-rules` with a temporary `CODEX_HOME`
  that holds only the borrowed login.

`--with-user-harness` never changes these.

## Subscription login

AHL uses the CLI you have installed under your subscription, so a run costs no API
usage. For Codex, the saved login is copied into each temporary `CODEX_HOME` with
mode `0600` before the session and deleted after it. When the run refreshed the token
and the real `auth.json` is still byte-identical to what was copied, the refreshed
file atomically replaces it; when the real file changed meanwhile it is left alone.
Contents are never printed or recorded: receipts say only `subscription_login_copied`,
`subscription_login_refreshed` or `not_staged`. A saved login takes precedence over
`CODEX_API_KEY`, which AHL then removes from the session environment because it
would bill the API; with no saved login `CODEX_API_KEY` is the alternative. A login
kept in the operating-system keyring instead of `auth.json` is not copied.
Claude Code's login lives outside the settings files and keeps working with
`--setting-sources project`; AHL requires no `ANTHROPIC_API_KEY`, but an exported one
is still passed through and Claude Code prefers it.

## Your installed harness

`--with-user-harness` (on `ahl improve`, `ahl watch` and `ahl skills invoke`) runs the
trials of both arms under the harness installed on the machine, for a more precise
evaluation of what you actually use. Claude Code then loads your settings, plugins,
hooks and MCP servers (only the write grant stays); Codex uses the real `CODEX_HOME`.
Both arms see the same layers, so the comparison stays paired, but results depend on
your machine, and your hooks and plugins run during trials. Each trial records
`user_harness: included` or `excluded`, as do the run's `runtime` and `isolation`
records.

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

- With `--with-user-harness` the descriptor lists the user layers as inherited and
  requests no exclusion.
- Claude Code inherits authentication stored outside settings files, project and
  managed permission rules and sandbox settings, managed settings and hooks, and
  allowlisted environment variables (or the full host environment with
  `--inherit-env`). User `CLAUDE.md`, auto memory and user plugins and skills are unknown.
- Codex inherits allowlisted environment variables (or the full host environment
  with `--inherit-env`), system configuration layers and `HOME/.agents/skills`. Its
  `credentials` field reports the copied subscription login. Hooks without trust entries are unknown.

## Reporting

Report isolation as the descriptor states it. An `inherited_unverified` status
means user and local layers may have influenced the trial; do not describe the
run as isolated or as clean of user configuration. A native run that observes a
request at the pinned version is the only way to move an item from unverified to
observed; until then, record effective settings as unknown. This is not network
isolation or a hostile-agent security boundary.
