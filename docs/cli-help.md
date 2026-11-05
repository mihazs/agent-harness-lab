# Discover CLI contracts before execution

Use installed command help as the reference for its required inputs, schemas,
defaults, units, side effects and exit states:

```sh
ahl --help
ahl analyze --help
ahl sources --help
ahl sources lineage --help
ahl bank-review --help
ahl bank-review qualify --help
```

Help exits before configuration writes, provider requests, worker execution,
downloads, source collection or credential access. Nested commands expose their
own help even when execution would require global options such as `--store`.

Start automation with read-only diagnostics or a configuration preview:

```sh
ahl doctor . --json
ahl init . --non-interactive --json
```

`init` creates `ahl.json` only with `--write`, and preserves existing files.
Doctor reports the observations it can make locally; authentication and unobserved
runtime properties remain unknown. Explicit `--probe-runtime` authorizes its
bounded offline probe, rather than an agent or model call.

An explicit capture avoids implicit home discovery. Supply the adapter matching
the capture schema; native profiles additionally require their pinned version:

```sh
ahl analyze . --events ./events.jsonl --format normalized
ahl improve . --agent codex --timeout 600
ahl watch . --agent codex --timeout 600 --once
```

The improvement examples require a trusted, supported, separately authenticated
runtime and may spend model resources. JSON receipts and private artifacts carry
the actual outcome; help text and declared configuration never grant authority or
certify success. Behavioral candidates remain recommendations. Explicit
`--repair-contracts` is a separate bounded structural application path; `watch`
has no such option and never edits harness files. `ahl improve --help` and
`ahl watch --help` list what each command changes and never changes.

Capture stdout and inspect the process exit status. Workflow exits distinguish
completed work (0), execution/interruption/budget failures (1), invalid input (2)
and blocked/inconclusive work (3). Low-level tools and setup commands describe their
own exit meanings. Keep run outputs private; hashes and opaque identifiers are
not publication permission. The optional classifier benchmark accepts existing
local model directories and does not download models.

Run recursive help and existing subprocess smoke checks with:

```sh
task test:cli:help
```
