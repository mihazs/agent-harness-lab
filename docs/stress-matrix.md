# First-use stress matrix

Reference for the failure-injection suite that checks every scenario class of a first
`ahl improve` run ends with a typed status and reason, never as a bare `no_change`, a
crash trace or an untyped error. It sits on top of the
first-use scenario matrix (`tests/integration/improve/test_first_use_matrix.py`) and the
[typed exit receipts](typed-exit-receipts.md): those fix what a single scenario must
say, this matrix injects the failures around them.

Everything is synthetic. Each cell builds a project, session stores and a fake
runtime under a temporary directory, runs the real `ahl improve` entry in process
and reads only what a user could read: the receipt on stdout, the exit code and
stderr. No cell sleeps, opens a network connection, reads a real home directory or
needs a real model. A timeout is the fake runtime advancing the budget clock the
workflow reads, not time passing.

## The end-state rule

Every cell must end with all of these. The helper is `end_state` in
`tests/support/stress_matrix.py`.

- One JSON receipt on stdout with a `status` from the closed set and a non-empty `reason`.
- The process exit code the `status` maps to.
- On a non-zero exit, an `exit_receipt` whose `code` matches and whose `next_step` is non-empty.
- No traceback on stderr.
- A `no_change` that carries a remediation, ranked `next_steps` or the per-cause
  `findings_not_candidates`, so it is never a bare "nothing to do".
- No lock left behind by a run that ended.

A cell proves the run ends typed. Where the end state is decided by the product and not by the
fake runtime, the cell also pins the exact status and reason listed below.

## Injected runtime faults

The fake runtime reads one fault from its mode file and fires it at one stage.

| Fault | What the runtime does |
| --- | --- |
| `timeout` | Advances the budget clock far past the whole budget, then exits 124. |
| `killed` | Sends itself `SIGKILL`. |
| `garbage` | Writes bytes that are not text to stdout and stderr, then exits 0. |
| `silent` | Exits 0 with no output and no files. |
| `oversized` | Writes 24 MiB to stdout, then exits 0. |
| `crash` | Prints traceback-like text to stderr and exits 70. |
| `interrupt` | Sends `SIGINT` to the process that runs `ahl`, which is a user's Ctrl-C. |

Each runs at preparation, at evaluation construction and in every trial, for both the Codex and
the Claude runtime protocol.

## Matrix

| Scenario class | Injected | End state | Exit |
| --- | --- | --- | --- |
| Runtime timeout in preparation | `timeout` | `budget_exhausted` / `budget_exhausted` | 1 |
| Runtime timeout in construction | `timeout` | `budget_exhausted` / `evaluation_construction_timeout` | 1 |
| Runtime killed, garbage, silent, oversized or crash in preparation or construction | five faults | `execution_error` / `execution_failed` | 1 |
| Any runtime fault in every trial | six faults | `inconclusive` / `evaluation_uninformative` | 3 |
| Corrupt session beside usable ones | truncated line, binary, empty file, NUL bytes, invalid UTF-8, rows that are not objects, one 20 MiB line | The corrupt session is skipped; analysis and verdict equal those of the usable sessions: `blocked` / `no_outcome_evidence`, no model call | 3 |
| History of only corrupt sessions, or empty history | same files, or none | `blocked` / `history_no_matching_sessions` with a remediation, no model call | 3 |
| Duplicated history | one session copied three times | One independent root, never three | 3 |
| Symlinked instruction file | live, dangling, looping or pointing at a directory | `invalid_input` / `unsupported_instruction_layout`, no model call | 2 |
| Instruction file that cannot be staged | a directory, or over the snapshot size limit | `invalid_input` / `run_input_rejected`, no model call | 2 |
| Instruction file with unusual content | invalid UTF-8, NUL bytes, empty | A typed end state; which one depends on the runtime | 1 or 3 |
| Read-only harness | read-only instruction file, read-only project directory | Runs like a writable one; the project file keeps its content and mode | 3 |
| Project frozen with `.ahl-read-only` | the marker file | `invalid_input` / `invalid_usage`, no model call | 2 |
| Concurrent runs, one project | the first run's runtime starts a second `ahl improve` | Second: `conflict` / `workspace_busy`; the first finishes | 1 |
| Concurrent runs, two projects, one state home | the same, on another project | Neither is blocked | n/a |
| Runtime killed mid-run | `killed` in trials | No lock remains; the next run is not busy | n/a |
| Lock left by a dead `ahl` process | a lock file naming a dead PID | `conflict` / `workspace_busy` with the removal step | 1 |
| Interruption | `interrupt` at each stage | `interrupted` / `interrupted`; the lock is released | 1 |
| Resume after an interruption | `--resume RUN_ID` with no completed proposal | `blocked` / `resume_requires_intervention`; the next step says to rerun the command | 3 |
| Resume of an unknown or malformed run ID | missing, traversal, empty, oversized, wrong prefix | `invalid_input`; no operating-system error text | 2 |
| Construction overrun | construction longer than its time box | `budget_exhausted` / `evaluation_construction_timeout` | 1 |
| Refinement no longer affordable | a worse first candidate with the budget spent | `budget_exhausted` / `refinement_budget_unavailable` | 1 |
| Tiny budget of 1, 2 or 5 seconds | real wall clock | A typed end state; the class depends on where the budget runs out | 1, 2 or 3 |

## What the matrix found

Each of these ended typed but wrongly, or untyped, and now has a regression test.

- A runtime whose output was not valid UTF-8 aborted the run with `invalid_input` and exit 2, which blames the user's input. The output is now decoded with replacement characters, so the run ends as an execution error.
- A dangling or looping instruction symlink was not listed as a harness file, so the run went on without it. It is now refused like any symlinked instruction file.
- A read-only instruction file made the staged candidate copy read-only too, so the preparation call was spent and the proposer could not write its candidate. The staged copy is now made writable for the proposer and gets its original modes back.
- `--resume` with a run ID that does not exist ended as an operating-system error with a raw errno. It is now a rejected input.
- A resume with no completed proposal carried a generic next step. It now says to rerun the command.

## Limits

- Only the cooperative Codex and Claude runtimes are injected; the controlled Responses route is not.
- The captured output of a cooperative runtime has no size cap in the runner. The matrix injects 24 MiB, not an unbounded stream.
- A lock left by an `ahl` process that was itself killed is not reclaimed automatically; the typed `workspace_busy` receipt names the removal step.
- A broken symlink inside a skill bundle is not covered by the instruction-file check.
- Real wall-clock budgets pin typedness only, because where a small budget runs out depends on the machine.
- Real-runtime and real-project runs are field validation, outside this suite.

## Run it

```
python -m pytest tests/integration/improve/test_stress_matrix.py \
  tests/unit/workflow/test_harness_files.py tests/unit/workflow/test_writable_candidate.py
python -m pytest skills/harness-evaluation/scripts/test_run_evaluation.py -k UndecodableOutput
```
