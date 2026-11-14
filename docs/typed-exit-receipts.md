# Typed exit receipts

Reference for how every non-zero exit of `ahl improve` ends. The same entry point
serves `ahl skills invoke`, `ahl analyze` and `ahl watch`, so they share it. A
consumer that reads only the machine-readable result can tell a usage error, a
budget exhaustion, an unsupported input and a busy workspace apart. For how the
budget is split by stage see [stage budgets](budget-stages.md).

## The receipt

For `ahl improve` and `ahl watch`, an exit before a run exists prints one JSON object
on stdout, and a run that ended carries the same fields in its receipt (`workflow.json`
and stdout). Human text stays on stderr. `ahl analyze`, and the refusals that happen
before a command is parsed (a managed-preparation session, a protected destination),
keep stdout empty and print the same object as one `ahl: exit_receipt=...` stderr line.
The run's own `output` field, when present, is the pre-existing private run location,
not part of the typed block.

`ahl watch` seals every result it prints, including a run that returned normally and one
that failed on invalid input, and an interrupt prints an `interrupted` receipt. When `--once`
ends without a fresh run and the checkpointed disposition is non-zero, stdout carries that
disposition as a typed receipt with `fresh_run: false`, so a replayed failure is never
mistaken for a new one.

| Field | Meaning |
| --- | --- |
| `status` | One of the workflow statuses; the process exit code follows it. |
| `reason` | One reason from the vocabulary below, or an existing registered reason (for example a manifest contract reason). Never null on a non-zero exit. |
| `exit_receipt.profile` | `ahl-typed-exit-v1` |
| `exit_receipt.code` | The process exit code. |
| `exit_receipt.stage` | Where it happened: one of `usage`, `workspace`, `harness_discovery`, `preparation`, `construction`, `trials`, `refinement`, `run`, or the named contract stage. |
| `exit_receipt.next_step` | Content-free corrective step, with command shapes written with placeholders such as `PROJECT` and `RUN_ID`. |
| `budget_stages` | Allotted and spent seconds per stage, when the invocation fixed a budget. |

The typed block, the reason and the stage budget carry no path, project name or file
content. An unknown status keeps its name, gets reason `run_incomplete` and exits 1,
never 0.

## Exit codes

`0` completed; `1` execution, conflict, interruption or budget; `2` usage, input or setup
error (any operating-system or network error before a run is `setup_error`/`system_error`; only a manifest
contract error keeps its own registered reason); `3` blocked or inconclusive. Exit `2` stays reserved for usage, input and setup errors,
and they carry a typed reason too. A busy workspace is a `conflict` (`1`), not a usage
error.

## Vocabulary

| Reason | Status (exit) | Default stage | Next step |
| --- | --- | --- | --- |
| `invalid_usage` | `invalid_input` (2) | `usage` | Correct the option or project argument named on stderr, then rerun; ahl improve --help lists every option. |
| `usage_conflict` | `invalid_input` (2) | `usage` | Resume with no other option: ahl improve PROJECT --resume RUN_ID |
| `protected_destination` | `invalid_input` (2) | `usage` | Choose an output or state location outside the project and its history, then rerun. |
| `unsupported_instruction_layout` | `invalid_input` (2) | `harness_discovery` | Make each instruction file a regular file inside the project instead of a symbolic link, then rerun. |
| `run_input_rejected` | `invalid_input` (2) | `run` | Read the rejected input named on stderr, correct it, then rerun; the private receipt of the run records the stage. |
| `workspace_busy` | `conflict` (1) | `workspace` | Wait for the active run on this project to finish, or resume it, then rerun; if none is active, check the recorded PID before removing the stale lock. |
| `run_conflict` | `conflict` (1) | `run` | Read the conflict recorded in the private receipt of the run, resolve it, then rerun. |
| `budget_exhausted` | `budget_exhausted` (1) | `run` | Raise the whole-run budget with --timeout SECONDS (or the construction share with --construction-fraction FRACTION), then rerun. |
| `execution_failed` | `execution_error` (1) | `run` | Read the stage error recorded in the private receipt, fix the runtime or environment problem it names, then rerun. |
| `system_error` | `setup_error` (2) | `run` | Fix the filesystem or operating-system condition reported on stderr, then rerun. |
| `interrupted` | `interrupted` (1) | `run` | Rerun the command, or finish a completed proposal without a model call with: ahl improve PROJECT --resume RUN_ID |
| `proposal_invalid` | `invalid_proposal` (1) | `preparation` | Read the proposal validation recorded in the private receipt, then rerun so preparation writes a valid proposal. |
| `evaluation_run_failed` | `evaluation_failed` (1) | `trials` | Read the failed evaluation records in the private receipt, fix the cause they name, then rerun. |
| `candidate_regressed` | `blocked` (3) | `trials` | Read investigation.json in the private run output: its cause says whether the candidate or the pipeline made the paired score worse; revise or discard the candidate, or fix a pipeline cause and add its regression test, then rerun. |
| `run_incomplete` | `incomplete` (1) | `run` | Read which evidence is missing in the private receipt of the run, restore it, then rerun. |
| `intervention_required` | `requires_intervention` (1) | `run` | Read the intervention the private receipt records, perform it, then rerun. |
| `setup_failed` | `setup_error` (2) | `usage` | Correct the setup problem reported on stderr, then rerun. |
| `evidence_inconclusive` | `inconclusive` (3) | `run` | Read the incomplete evidence recorded in the private receipt before drawing any conclusion, then rerun with more evidence. |
| `prerequisite_blocked` | `blocked` (3) | `run` | Satisfy the prerequisite recorded in the private receipt, then rerun. |
| `capability_unavailable` | `blocked` (3) | `run` | Run where the required filesystem capabilities exist; there is no unsafe fallback. |
| `nested_invocation_refused` | `blocked` (3) | `usage` | Run ahl from the user shell, not from a managed preparation session. |
| `legacy_attempted` | `legacy_attempted` (3) | `run` | Read the recorded legacy operation in the private receipt before retrying. |

`budget_exhausted` names the stage that consumed the budget in `exit_receipt.stage`
(`preparation`, `construction`, `trials` or `refinement`) when the run observed it.

## Where it is enforced

`agent_harness_lab/typed_exit.py` owns the vocabulary; `exit_guidance.py` reuses its
next steps as remediation. The regression catalog in
`tests/integration/cli/test_typed_exit_receipts.py` derives the exit paths from the
source of the entry point and fails when a non-zero exit lacks a typed reason.
