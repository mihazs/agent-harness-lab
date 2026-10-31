# Harness Improver

Project-agnostic skills and tools for diagnosing agent workflows and evaluating
improvements against observable outcomes. Requires Python 3.11+; the miner and
tests use the standard library. Agent trials additionally require an authenticated
Codex CLI and its existing runtime configuration.

## What lives here

- `skills/harness-self-improvement`: diagnosis across distinct failure mechanisms,
  candidate selection, adoption and coverage-based completion.
- `skills/harness-evaluation`: paired execution, process mining, learning-method
  selection, independent graders and transfer checks.
- `tools/link_skills.py`: reversible global installation using directory symlinks.

The skills describe when to use process counts, trace clustering, sentence
embeddings, uncertainty sampling and trajectory credit assignment. They do not
install ML models or train a model automatically. Primary academic sources and
their practical limits are in
[evidence-to-design.md](skills/harness-evaluation/references/evidence-to-design.md).

## Check

```sh
python -m unittest discover -s skills/harness-evaluation/scripts -p 'test_*.py'
python -m unittest discover -s tools/tests -p 'test_*.py'
```

## Mine normalized events

Prepare redacted JSONL with `case_id`, `event_id` and `activity`. Optional fields
are `root_case_id`, `actor` and timezone-aware ISO `timestamp`. Parent/child lineage
must be verified upstream. The miner ignores extra fields; it is not a sanitizer.

```json
{"case_id":"case-1","event_id":"1","activity":"validate","actor":"tool"}
{"case_id":"case-1","event_id":"2","activity":"stop","actor":"agent"}
```

```sh
python skills/harness-evaluation/scripts/mine_traces.py events.jsonl --exclude unknown
```

Output separates raw adjacency from filtered, run-collapsed projections and counts
independent root-case support. Stops do not imply success; edges do not prove
causality. Read the [diagnosis method](skills/harness-evaluation/references/trace-diagnosis.md)
before interpreting large histories.

## Evaluate changes

Use the [execution contract](skills/harness-evaluation/references/execution.md).
Snapshot baseline and candidate skills outside active skill discovery, freeze
fixtures and external graders, then run:

```sh
python skills/harness-evaluation/scripts/run_evaluation.py \
  --manifest /path/to/manifest.json \
  --baseline /path/to/baseline --candidate /path/to/candidate \
  --output /path/to/new-results
```

Each task runs in a fresh workspace. Trials are sequential and timeout-bounded;
interruption kills and reaps the owned child process group. Inherited tools and
hooks remain active. This is cooperative fixture isolation, not a security or
network sandbox. Do not use real credentials or production mutations in fixtures.

## Link global skills

```sh
python tools/link_skills.py
```

The default destination is `~/.agents/skills`. Existing same-name directories move
to a unique backup below `~/.local/state/harness-improver/backups`; foreign symlinks
are rejected. Rerunning is a no-op. Keep this checkout in place while the global
skills point to it. To undo installation, inspect the two links, remove only links
still pointing to this checkout, and move the corresponding backed-up directories
back into the global destination. Preserve intervening changes.

Keep private traces, evaluation outputs and generated workspaces outside the skill
tree; an ignored `artifacts/` directory is available for local results. Do not
commit them. No project-specific infrastructure rules belong in these skills.

## Diagnose traces offline

```sh
python tools/diagnose.py --events redacted-events.jsonl --output artifacts/diagnosis
python tools/benchmark_models.py --help
```

Diagnosis reports evidence coverage, recurring short sequences and duplicate
observations. It does not infer task success, wasted calls, or instruction
staleness from repetition alone. Inputs must already be redacted. Native Codex
and ai-memory exports can be normalized as metadata; missing information stays
unknown. Outputs refuse overwrite and default to a 100,000-event bound.

Optional local classifier experiments use `pip install '.[ml]'`. Models must be
provided locally; no hosted inference is required. See the benchmark's help for
its labeled JSONL contract. Keep related root cases in one split. Sparse or
unverifiable labels cannot support reliability claims. Learned methods remain
experimental until they outperform the simpler baseline on held-out evidence.

## Contributing and governance

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md),
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), and [LICENSE](LICENSE).
This project is maintained by repository owner @mihazs. Contributions are reviewed
through pull requests. Published research motivates hypotheses; retained executable
comparisons determine adoption. The default tools have no third-party dependencies;
optional ML dependencies are isolated from ordinary tests.

## Status and evidence limits

Experimental, version 0.1.0. Pattern discovery is implemented; proving necessity,
causal responsibility, instruction staleness, and extracted-skill utility still
requires reviewed contracts and outcome-preserving interventions. The tools do
not infer those judgments from frequency or model confidence. See
[research grounding](skills/harness-evaluation/references/evidence-to-design.md)
and [operational criteria](skills/harness-evaluation/references/trace-diagnosis.md).

No hosted model receives private history by default. Downloaded model files and
private evaluation data belong under ignored `artifacts/`, never in the skills.

### Reproduce a local adapter smoke check

```sh
python tools/diagnose.py --events examples/events.jsonl --output artifacts/example-diagnosis
python examples/classifier_fixture.py > artifacts/classifier-fixture.jsonl
python tools/benchmark_models.py artifacts/classifier-fixture.jsonl --method baseline
# With the optional ML extra installed:
python tools/benchmark_models.py artifacts/classifier-fixture.jsonl --method tfidf
```

The fixture intentionally repeats an easy vocabulary across disjoint root IDs.
Passing it proves adapter execution, not semantic generalization. Embeddings and
NLI additionally require `--model-path /path/to/local/model`; NLI requires
`--question 'The command failed.'`. Downloads are a separate setup step and never
send the trace corpus to the model host. Real-history labels require verified
contracts/outcomes; metadata alone does not supply them.

Executed checks and their limits are recorded in [initial validation](docs/validation.md).
