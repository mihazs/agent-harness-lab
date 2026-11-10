# Command overview

[Workflows](workflows.md) · [CLI help](cli-help.md)

| Command | Purpose |
| --- | --- |
| `ahl improve [project]` | Discover issues, stage repairs and evaluate; behavioral adoption is blocked |
| `ahl watch [project]` | Rerun recommendation-only evaluation when evidence or configuration changes; AHL never writes harness files |
| `ahl analyze [project]` | Automatically collect and analyze evidence without an agent |
| `ahl diagnose --events FILE --output DIR` | Diagnose an explicit event file |
| `ahl mine FILE` | Mine event transitions and recurring root-case support |
| `ahl evaluate --manifest FILE --baseline DIR --candidate DIR --output DIR` | Execute a prepared baseline/candidate experiment |
| `ahl benchmark FILE --method baseline` | Compare incident-classifier predictions; optional [frozen triage policy](diagnostic-triage.md) or `--profile diagnostic-corpus` for a [corpus development export](diagnostic-corpus.md#benchmark-input-profile) |
| `ahl skills --agent claude` | Link installed skills into a selected runtime's skill directory |

Run `ahl COMMAND --help` for the complete options. Low-level evaluation supports
`--agent claude` or `--agent codex`; preparation and evaluation never mix runtimes
within a pair. CLI executables can be selected with `--claude` or `--codex`.

The installed skills also work inside an existing agent session. They discover
improvement candidates themselves; an incident description or proposed candidate
is optional. In Claude Code their commands are `/harness-self-improvement` and
`/harness-evaluation`. During a CLI-managed run, the controller owns execution and
adoption; the skills must not launch recursive improvement controllers.
