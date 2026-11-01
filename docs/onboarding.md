# Offline readiness and project initialization

[Documentation index](README.md)

`ahl doctor PROJECT --profile offline --json` inspects Python, installed skill
assets, platform filesystem capabilities, project/configuration safety and the
selected bank's schema and manifest digest. It never reads fixture or oracle
bodies: `bank_contents` remains `not_checked`. An explicit bank audit or evaluation
still requires full content integrity verification. Metadata-only validation
cannot make a bank eligible for certification.
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

`ahl evaluate --config /path/to/ahl.json` explicitly consumes configuration in
addition to the existing required evaluation arguments. There is no automatic
discovery. The config file's directory defines the project for relative bank paths.
The run validates the selected bank's digest and compares requested task metadata,
fixtures and complete oracle bundles against a pristine export. A manifest's
claimed `bank_digest` alone is insufficient. Injected harness paths must be a
subset of the configured allowlist; configured runs do not support named skill
injection. Offline mounted harness trees may contain only allowlisted files.

An offline profile permits only `--agent offline`, with its existing explicit
fixture-command contract. A cooperative profile requires the requested agent to
match the configuration and uses its explicitly configured executable. Conflicting
executable overrides, unavailable binaries and unsupported isolated profiles block
before runtime execution. Authentication, model settings and full containment
remain unverified; configuration cannot enable automatic promotion.

Configured seconds/trial caps can only lower the manifest's ceilings. Preflight,
including bank export/comparison, consumes the same wall-clock budget. In-process
filesystem preparation remains non-preemptible; these are admission/time limits,
not provider cost/token or aggregate CPU enforcement. Trial counts refer to task
executions, not metadata version checks or graders. Existing fixed paired planning
must fit the reduced trial ceiling, and planned task/root/family identities must
match the selected bank before probing. Private results include the declared
config, canonical JSON digest, bank digest, requested runtime and applied ceilings.
Configuration/bank changes stop later dispatch and invalidate the final result.
Input hashes are frozen before any capability/version subprocess. Configured
harness and fixture digests also cover file modes; changes during a version
query cannot become a new accepted starting point for task execution.

Existing analysis/improvement/watch commands still retain their explicit CLI
behavior and do not consume `ahl.json`. Authentication and agent runtime versions
remain unknown because this offline interface has no supported safe agent probe.
Initialization itself authorizes no execution or permission changes. Doctor/init
start no installation, login, inference, download, background service or schedule.
