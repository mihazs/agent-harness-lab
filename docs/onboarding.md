# Offline readiness and project initialization

[Documentation index](README.md)

`ahl doctor PROJECT --profile offline --json` inspects Python, installed skill
assets, project/configuration safety and the selected bank's schema and digest.
It does not run an agent, inspect authentication/session files, execute hooks,
connect to services or change the project. Human output names prerequisites and
safe remediations. Versioned JSON checks contain ID, status, profile requirement,
evidence kind, bounded observed value, remediation and capability version.
Statuses are `pass`, `warn`, `blocked`, `unknown` and `not_checked`. Exit 0 means
requested readiness passed; 2 means invalid invocation/configuration; 3 means a
required capability is unavailable or unverified. Optional missing runtimes do
not block offline readiness. Doctor never echoes configuration or bank contents.

Cooperative and isolated profiles keep authentication and runtime versions
unverified. Executable presence does not establish authentication.
`--probe-runtime` permits only a bounded offline namespace probe using a trusted
system `/usr/bin/bwrap` on Linux; it never launches the configured agent or searches
PATH. A successful partial probe cannot establish full isolated readiness:
inference brokering, full role orchestration, pinned images and aggregate resource
controls remain prerequisites.

## Preview and create configuration

Use a new disposable directory for this synthetic example:

```sh
demo_project=$(mktemp -d)
ahl doctor "$demo_project" --profile offline --json
ahl init "$demo_project" --non-interactive --json
ahl init "$demo_project" --non-interactive --write --json
ahl doctor "$demo_project" --json
```

Preview leaves the directory unchanged. `--write` creates only `ahl.json`, mode
0600, after configuration/bank preflight. It refuses existing files and linked
paths. Exclusive atomic installation and cleanup preserve existing content and
prevent partial JSON. Repeating the write fails. Instructions, skills, global
configuration and installation state are unchanged.

`--config-input SETTINGS.json` supplies optional initialization fields.
Non-interactive mode never reads stdin. A terminal prompts only for omitted
required choices; `q`, `cancel`, EOF or Ctrl-C cancel before creation. Non-TTY
callers must use `--non-interactive`.

| Version-1 field | Contract |
| --- | --- |
| `schema_version` | Integer 1 |
| `profile` | `offline`, `cooperative` or `isolated` |
| `runtime` | `name`: `none`, `codex` or `claude`; `executable`: null for none, otherwise explicit absolute trusted executable path |
| `bank` | `path`: `starter` or safe project-relative bank path; `sha256`: reviewed lowercase SHA-256 |
| `harness_paths` | Unique project-relative paths without traversal or `.git` |
| `limits` | Finite positive `seconds` and positive integer `trials` |
| `recommendation_only` | Must be true; cannot enable automatic promotion |

Unknown/duplicate keys, invalid caps and incompatible combinations are rejected.
Defaults select offline/no runtime, the packaged starter bank with its current
digest, no harness paths, 2700 seconds and 20 trials. Never put tokens, private
transcript paths or executable configuration in this file. Reads are bounded and
reject symlinks, hardlinks and non-regular files.

## Current integration boundary

This stage implements schema, readiness, initialization and CLI/installed-layout
behavior for #36. Only `doctor` and `init` currently consume `ahl.json`. Existing
analysis/improvement/watch commands retain their explicit CLI behavior; the file
does not authorize execution or expand scope. Effective-configuration consumption
and provenance in actual execution, plus a verified harmless agent version/auth
probe, remain unfinished. Initialization does not enforce execution budgets or
permissions. These commands start no installation, login, inference, download,
background service or schedule.
