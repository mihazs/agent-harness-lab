# First diagnosis: a repeated synthetic observation

[Documentation index](README.md) · [Installation](installation.md)

## Problem and input

A developer sees repeated reads and wants to inspect them before changing project
instructions. This independently constructed synthetic fixture contains four events:
two linked read-only call/result pairs in one case, with matching tool, input,
result and state identifiers. It contains no project history or real file contents.

Input: [repeated-observation.jsonl](../examples/repeated-observation.jsonl).
Full actual output: [repeated-observation-report.md](../examples/repeated-observation-report.md).

## Reproduce

With Git, a POSIX shell and Python 3.11+, clone the public repository and enter it:

```sh
git clone https://github.com/mihazs/agent-harness-lab.git
cd agent-harness-lab
python3 -m agent_harness_lab --help
python3 -m agent_harness_lab diagnose --events examples/repeated-observation.jsonl --output /tmp/ahl-first-diagnosis
cat /tmp/ahl-first-diagnosis/report.md
diff -u examples/repeated-observation-report.md /tmp/ahl-first-diagnosis/report.md
```

The command exits 0 and prints no diagnosis summary to the terminal; open the
written report. `diff` exits 0 without output when the report matches. Use a fresh
output directory on every attempt. `findings.json` contains machine-readable results
and the input SHA-256. This explicit-input command does not inspect native sessions,
run model inference or modify instructions. No package installation is needed.

## Observed result and interpretation

The report contains four events, one case, one `duplicate_observation` finding and
zero recurring sequences. The finding references events 2 and 4; it has status
`observed_pattern`. All four events specify tool/state, but none establishes task
outcome or verified lineage. One case does not meet the recurring-sequence threshold.

The report means the same observation was recorded at the same state. It does
not show the second read was unnecessary, establish cause, demonstrate a repair,
or prove better agent performance. The next useful question is whether a task
contract required that read and whether successful controls preserve it.

## Controls and environment

Validation used Linux and Python 3.12.14. The committed smoke test executes the
public command in a temporary directory and compares the complete report. Separate
controls change the second result, change state, or omit the input fingerprint;
each must suppress the duplicate finding. Reusing the output directory must fail
with exit 2. These are detector/CLI controls, not a model baseline/candidate experiment.

```sh
python3 -m unittest discover -s tools/tests -p 'test_quickstart.py'
```

The unchanged trace is the diagnostic baseline. Each mutated trace is a negative
control, not a candidate harness improvement. No model trial, independent held-out
experiment, cost saving or validated repair is claimed. Synthetic matching fields
are stipulated; real capture may omit or misidentify them. No uncertainty interval
is meaningful for this single constructed example.

## Next optional steps

`python3 -m agent_harness_lab bank --starter --list` inspects seven bundled synthetic
tasks without agents. It exits 3 because independence/review remains unverified for
certification; this is expected for the starter bank, not a setup failure.
For native formats see [telemetry](telemetry.md); for authenticated paired work read
[workflows](workflows.md) and [evaluation safeguards](evaluation-foundation.md).
Use [troubleshooting](installation.md#common-setup-problems) or open a synthetic-only
[issue](https://github.com/mihazs/agent-harness-lab/issues/new/choose) if reproduction fails.
