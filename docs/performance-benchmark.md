# Measure startup, throughput and memory

[Documentation index](README.md) · [Contribute](../CONTRIBUTING.md)

Use this when you change code on the command-line or `diagnose` path and want a
before/after number, or when you suspect a regression. It measures this checkout on
your machine with a synthetic trace. It uses the standard library only, needs no
agent, network or credentials, and does not run any model.

## Run it

```sh
task bench                       # about a minute on a quiet machine; writes artifacts/perf-bench/report.json
task bench -- --runs 25 --sizes 20000,80000
task bench:check                 # same run; exit 1 when a result exceeds tools/perf_budgets.json
```

`artifacts/` is ignored by Git, so reports stay local. `--output PATH` writes the JSON
elsewhere. Without Task, run `python3 tools/perf_bench.py` with the same flags.

## What it measures

| Measurement | How |
| --- | --- |
| Startup | `ahl --help` and `ahl COMMAND --help` for every top-level command, in a fresh process each: one untimed warm-up, then `--runs` timed runs (default 15), reported as median, minimum and maximum milliseconds. The command list is read from `ahl --help`, so a new command is measured without editing the benchmark; the alias `analysis` is listed there and measured as its own row. A bare `python -c pass` is the control. |
| Throughput | `ahl diagnose --events FILE` on 20,000, 80,000 and 240,000 synthetic events, `--repeats` runs each (default 3): median wall seconds and input megabytes per second. |
| Memory | The child's peak resident set size from `wait4`. On Linux a child starts with its parent's size, so no reading is below the driver's own. The driver streams its hashes to stay small, and the report records its peak as `driver_peak_rss_mb` (23 MB in the baseline run), far below every diagnose peak. |
| Output stability | SHA-256 of `findings.json` and `report.md` for each run. `outputs_identical_across_runs` is true when every run gave the same pair, false when two differ, and null when `--repeats 1` compared nothing (`task bench:check` reports null as not measured). The digests let you compare two commits. |

The trace is generated, not recorded. A seeded generator emits normalized events:
tool call/result pairs over a small pool of fingerprints, so repeated identical
reads occur, about one call in five mutating state. A case is 2 to 19 steps and about
one step in five is an assistant turn without a call, so a case holds about 8 calls
on average and anywhere from 0 to 19. The same seed and event count always give the
same bytes (`input_sha256` in the report). A longer corpus starts with the shorter one.

## Compare two versions

Run the benchmark on both commits on the same machine, close to each other in time,
with other heavy jobs stopped, and compare medians. The report records the Python
version, CPU count, load average before and after, and the commit. Treat differences
inside the min–max spread as noise. For a statement about a small change, increase
`--runs`.

## Budgets

`tools/perf_budgets.json` holds regression guards, not goals. Startup limits are
ratios to the bare interpreter so a faster or slower machine mostly cancels; every
top-level command has one, and a test fails when the CLI lists a command that has none.
The diagnose limits are loose absolute limits. `task bench:check` is deliberately not part
of `task test`, because wall time is not reliable on shared CI hosts. When a change
earns an improvement, tighten the matching limit in the same pull request.

## Baseline

One run on a Linux x86-64 workstation with Python 3.14.9 while other jobs were running
(load average about 3 to 5), 15 startup runs per command and 3 diagnose runs per size.
Every top-level command was measured, including the `analysis` alias.

| Command (`--help`) | Median ms | Min–max ms |
| --- | --- | --- |
| bare interpreter (control) | 8.9 | 8.4–9.8 |
| `ahl` | 52.8 | 51.3–54.0 |
| `ahl analysis` | 51.9 | 49.4–55.2 |
| `ahl analyze` | 51.8 | 49.8–54.7 |
| `ahl annotation-plan` | 53.6 | 51.6–56.1 |
| `ahl audit` | 84.4 | 80.1–91.8 |
| `ahl bank` | 49.1 | 47.3–53.9 |
| `ahl bank-interchange` | 54.4 | 52.1–59.7 |
| `ahl bank-review` | 68.9 | 64.3–76.4 |
| `ahl benchmark` | 55.4 | 52.8–56.9 |
| `ahl benchmark-report` | 47.0 | 45.2–49.3 |
| `ahl campaign` | 53.0 | 50.1–54.7 |
| `ahl compare` | 54.0 | 52.4–56.3 |
| `ahl config` | 47.7 | 44.5–49.2 |
| `ahl diagnose` | 55.4 | 52.4–59.7 |
| `ahl doctor` | 54.3 | 52.8–56.7 |
| `ahl evaluate` | 65.8 | 63.4–68.2 |
| `ahl experiment` | 47.1 | 44.9–52.0 |
| `ahl experiment-history` | 67.8 | 64.9–72.1 |
| `ahl grader-correction` | 64.7 | 61.8–67.4 |
| `ahl improve` | 51.9 | 49.5–53.5 |
| `ahl incident-search` | 47.1 | 44.8–51.2 |
| `ahl init` | 53.3 | 51.5–56.7 |
| `ahl memory` | 51.8 | 51.0–55.2 |
| `ahl mine` | 48.4 | 45.4–49.4 |
| `ahl promotion` | 46.6 | 45.8–48.2 |
| `ahl regression` | 47.2 | 45.7–49.1 |
| `ahl regression-review` | 63.3 | 59.2–70.3 |
| `ahl research-simulate` | 46.7 | 44.8–47.9 |
| `ahl rollback` | 76.3 | 73.2–82.6 |
| `ahl sandbox-check` | 47.5 | 46.1–50.4 |
| `ahl skills` | 48.8 | 47.0–51.3 |
| `ahl sources` | 115.9 | 112.2–119.7 |
| `ahl update` | 65.9 | 62.8–68.0 |
| `ahl watch` | 52.2 | 50.3–53.2 |

| Events | Seconds | Peak MB | Input MB/s |
| --- | --- | --- | --- |
| 20,000 | 0.55 | 86 | 6.3 |
| 80,000 | 2.17 | 267 | 6.44 |
| 240,000 | 7.02 | 738 | 5.99 |

These numbers describe that run only. Memory grows roughly linearly with the event
count and time grows roughly linearly, so extrapolation beyond the measured sizes is
an assumption, not a result.

## Limits

- The corpus is synthetic and uniform. Real traces differ in field mix, case length
  and size, so absolute numbers will differ.
- Peak resident size is read from the operating system as the child's maximum; it
  counts the interpreter and imports, not only analysis data, and on Linux it cannot
  be lower than the driver's own peak when the child starts (see Memory above).
- Startup measures `--help` only, the cost of importing and parsing, not a command's
  work. Each command's row is one process per run, with the file cache warm.
- `wait4` resource usage is POSIX only; the benchmark does not run on Windows.
- It does not measure evaluation runs, sandboxed execution, network calls, model
  latency or the test suite.
- A hash match shows identical output for this corpus, not for every input.
