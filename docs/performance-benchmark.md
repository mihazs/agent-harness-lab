# Measure startup, throughput and memory

[Documentation index](README.md) · [Contribute](../CONTRIBUTING.md)

Use this when you change code on the command-line or `diagnose` path and want a
before/after number, or when you suspect a regression. It measures this checkout on
your machine with a synthetic trace. It uses the standard library only, needs no
agent, network or credentials, and does not run any model.

## Run it

```sh
task bench                       # about a minute; writes artifacts/perf-bench/report.json
task bench -- --runs 25 --sizes 20000,80000
task bench:check                 # same run; exit 1 when a result exceeds tools/perf_budgets.json
```

`artifacts/` is ignored by Git, so reports stay local. `--output PATH` writes the JSON
elsewhere. Without Task, run `python3 tools/perf_bench.py` with the same flags.

## What it measures

| Measurement | How |
| --- | --- |
| Startup | `ahl [COMMAND] --help` in a fresh process, one untimed warm-up, then `--runs` timed runs (default 15), reported as median, minimum and maximum milliseconds. A bare `python -c pass` is the control. |
| Throughput | `ahl diagnose --events FILE` on 20,000, 80,000 and 240,000 synthetic events, `--repeats` runs each (default 3): median wall seconds and input megabytes per second. |
| Memory | The child process's own peak resident set size (`wait4`), so the benchmark's memory is excluded. |
| Output stability | SHA-256 of `findings.json` and `report.md` for each run. `outputs_identical_across_runs` must be true; the digests let you compare two commits. |

The trace is generated, not recorded. A seeded generator emits normalized events:
tool call/result pairs over a small pool of fingerprints, so repeated identical
reads occur, about one call in five mutating state, with 2 to 19 calls per case. The
same seed and event count always give the same bytes (`input_sha256` in the report).
A longer corpus starts with the shorter one.

## Compare two versions

Run the benchmark on both commits on the same machine, close to each other in time,
with other heavy jobs stopped, and compare medians. The report records the Python
version, CPU count, load average before and after, and the commit. Treat differences
inside the min–max spread as noise. For a statement about a small change, increase
`--runs`.

## Budgets

`tools/perf_budgets.json` holds regression guards, not goals. Startup limits are
ratios to the bare interpreter so a faster or slower machine mostly cancels. The
diagnose limits are loose absolute limits. `task bench:check` is deliberately not part
of `task test`, because wall time is not reliable on shared CI hosts. When a change
earns an improvement, tighten the matching limit in the same pull request.

## Baseline

One run on a Linux x86-64 workstation with Python 3.14.9 while other jobs were running
(load average about 5), 15 startup runs and 3 diagnose runs:

| Command (`--help`) | Median ms |
| --- | --- |
| bare interpreter | 9.2 |
| `ahl` | 55.9 |
| `ahl mine` | 50.6 |
| `ahl diagnose`, `analyze`, `doctor` | 56–57 |
| `ahl regression-review` | 68.0 |
| `ahl experiment-history` | 70.4 |
| `ahl sources` | 125.2 |

| Events | Seconds | Peak MB | Input MB/s |
| --- | --- | --- | --- |
| 20,000 | 0.57 | 86 | 6.1 |
| 80,000 | 2.29 | 268 | 6.1 |
| 240,000 | 7.23 | 734 | 5.8 |

These numbers describe that run only. Memory grows roughly linearly with the event
count and time grows roughly linearly, so extrapolation beyond the measured sizes is
an assumption, not a result.

## Limits

- The corpus is synthetic and uniform. Real traces differ in field mix, case length
  and size, so absolute numbers will differ.
- Peak resident size is read from the operating system as the child's maximum; it
  counts the interpreter and imports, not only analysis data.
- `wait4` resource usage is POSIX only; the benchmark does not run on Windows.
- It does not measure evaluation runs, sandboxed execution, network calls, model
  latency or the test suite.
- A hash match shows identical output for this corpus, not for every input.
