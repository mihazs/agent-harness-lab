# Find where agents ignored instructions

Use this when you want to know which harness rules or user-prompt instructions your
captured sessions did not honor, with evidence you can follow back to the exact turn.
It reads files you name, runs offline with no model, and writes nothing unless you
pass `--output`.

The contracts are in the [instruction-adherence reference](instruction-adherence.md),
and the reasoning is in [why findings are separate and abstain](instruction-adherence-rationale.md).

## 1. Describe the sessions

`ahl instruction-adherence` takes a sessions file you prepared from captures you are
allowed to read. Each session lists its turns in order and says how complete the
capture is and which instruction files the agent had loaded:

```json
{
  "profile": "ahl-instruction-sessions-v1",
  "sessions": [{
    "session": "s1",
    "root": "r1",
    "capture": "complete",
    "loaded": {"project:AGENTS.md": "loaded"},
    "turns": [
      {"role": "user", "text": "Do not edit `config/prod.yml`."},
      {"role": "agent", "text": "Understood."},
      {"role": "tool_call", "tool": "Edit", "path": "config/prod.yml"},
      {"role": "tool_call", "tool": "Bash", "command": "git push --force origin main"}
    ]
  }]
}
```

Leave `capture` out, or say `unknown`, when you do not know whether the turns are the
whole session: an unknown capture is reported as unknown and decides nothing. Leave
`root` out when you do not know which sessions share a lineage; those sessions are
counted apart and never as independent roots. Give a `loaded` state only when you know
it (`loaded`, `truncated`, `not_loaded`); a source you do not list is `unknown`.

## 2. Run it

```sh
ahl instruction-adherence sessions.json --rules project=AGENTS.md
ahl instruction-adherence sessions.json --rules project=AGENTS.md --output report.json
```

The key in `loaded` is `LAYER:FILE-NAME`, so the file above matches
`project:AGENTS.md`. The default scope reads only `project` rule files; pass
`--scope global` or `--scope project+global` to read a `--rules global=FILE` file.

The JSON report goes to stdout (or to a new private file with `--output`, which never
overwrites). A short summary goes to stderr: counts by status, abstentions and the
calibration measurements. Exit status is 0 when a report was produced and 2 for invalid input.

## 3. Read the findings

| Status | Meaning | What to do |
| --- | --- | --- |
| `observed_violation` | A rule that was loaded, or a prompt instruction, was contradicted by a tool call | Inspect the turn named in `span`, then decide whether the instruction needs to be clearer or enforced |
| `not_loaded` | A call would have broken a rule that was not in the agent's context | Fix how the rule is loaded, not the agent; this is not an ignored rule |
| `conflict` | Two rules in force together require and forbid the same thing | Remove or reconcile one of them |
| abstentions | A possible violation, below the calibrated threshold | Treat as a lead only |

`evidence_sha256`, `instruction_ref` and `session_ref` are digests, so you can match
findings across reports without the report containing prompt text, paths or session
identifiers. Add `--private-detail` to include each instruction sentence (at most 240
characters) when you need to see which rule a digest names; the report is then still
private and never publishable.

## Limits to keep in mind

- Only a backticked command or tool, or a path, makes a sentence checkable. Anything else
  is counted as reviewable or unverifiable and produces no violation.
- A finding is a pattern with evidence, not a cause. It does not say why the agent
  deviated, and it does not measure whether a change would help.
- A user who repeats an instruction is a weak signal. It is reported as a possible
  violation only when no checkable evidence decides the instruction, and it is held back as
  an abstention unless the sample calibration admits it.
