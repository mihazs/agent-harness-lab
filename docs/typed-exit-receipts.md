# Typed exit receipts

Reference for how every non-zero exit of `ahl improve` ends. The same entry point
serves `ahl skills invoke`, `ahl analyze` and `ahl watch`, so they share it. A
consumer that reads only the machine-readable result can tell a usage error, a
budget exhaustion, an unsupported input and a busy workspace apart. For how the
budget is split by stage see [stage budgets](budget-stages.md).

## The receipt

An exit before a run exists prints one JSON object on stdout; a run that ended
carries the same fields in its receipt (`workflow.json` and stdout). Human text stays on
stderr.

| Field | Meaning |
| --- | --- |
| `status` | One of the workflow statuses; the process exit code follows it. |
| `reason` | One reason from the vocabulary below, or an existing registered reason (for example a manifest contract reason). Never null on a non-zero exit. |
| `exit_receipt.profile` | `ahl-typed-exit-v1` |
| `exit_receipt.code` | The process exit code. |
| `exit_receipt.stage` | Where it happened: one of `usage`, `workspace`, `harness_discovery`, `preparation`, `construction`, `trials`, `refinement`, `run`, or the named contract stage. |
| `exit_receipt.next_step` | Content-free corrective step, with command shapes written with placeholders such as `PROJECT` and `RUN_ID`. |
| `budget_stages` | Allotted and spent seconds per stage, when the invocation fixed a budget. |

A receipt never carries a path, a project name or file content. An unknown status is
sealed as `incomplete`, never as a success.

## Exit codes

`0` completed; `1` execution, conflict, interruption or budget; `2` usage or input
error; `3` blocked or inconclusive. Exit `2` is reserved for usage and input errors,
and they carry a typed reason too. A busy workspace is a `conflict` (`1`), not a usage
error.

## Vocabulary

| Reason | Status (exit) | Default stage | Next step |
| --- | --- | --- | --- |
| `invalid_usage` | `invalid_input` (2) | `usage` | Correct the option or project argument named on stderr, then rerun; ahl improve --help lists every option. |
| `usage_conflict` | `invalid_input` (2) | `usage` | Resume with no other option: ahl improve PROJECT --resume RUN_ID |
| `protected_destination` | `invalid_input` (2) | `usage` | Choose an output or state location outside the project, then rerun. |
| `unsupported_instruction_layout` | `invalid_input` (2) | `harness_discovery` | Make each instruction file a regular file inside the project instead of a symbolic link, then rerun. |
| `run_input_rejected` | `invalid_input` (2) | `run` | Read the private receipt of the run for the rejected input, correct it, then rerun. |
| `workspace_busy` | `conflict` (1) | `workspace` | Wait for the active run on this project to finish or resume it, then rerun; if none is active, check the recorded PID before removing the stale lock. |
| `run_conflict` | `conflict` (1) | `run` | Read the private receipt of the run, fix the cause it records, then rerun. |
| `budget_exhausted` | `budget_exhausted` (1) | `run` | Raise the whole-run budget with --timeout SECONDS (or the construction share with --construction-fraction FRACTION), then rerun. |
| `execution_failed` | `execution_error` (1) | `run` | Read the stage error recorded in the private receipt, fix the runtime or environment problem it names, then rerun. |
| `system_error` | `execution_error` (1) | `run` | Fix the filesystem or operating-system condition reported on stderr, then rerun. |
| `interrupted` | `interrupted` (1) | `run` | Rerun the command, or finish a completed proposal without a model call with: ahl improve PROJECT --resume RUN_ID |
| `proposal_invalid` | `invalid_proposal` (1) | `preparation` | Read the private receipt of the run, fix the cause it records, then rerun. |
| `evaluation_run_failed` | `evaluation_failed` (1) | `trials` | Read the private receipt of the run, fix the cause it records, then rerun. |
| `run_incomplete` | `incomplete` (1) | `run` | Read the private receipt of the run, fix the cause it records, then rerun. |
| `setup_failed` | `setup_error` (2) | `usage` | Correct the reported setup problem, then rerun. |
| `evidence_inconclusive` | `inconclusive` (3) | `run` | Read the private receipt of the run, fix the cause it records, then rerun. |
| `prerequisite_blocked` | `blocked` (3) | `run` | Satisfy the prerequisite recorded in the private receipt, then rerun. |
| `capability_unavailable` | `blocked` (3) | `run` | Run where the required filesystem capabilities exist; there is no unsafe fallback. |
| `nested_invocation_refused` | `blocked` (3) | `usage` | Run ahl from the user shell, not from a managed preparation session. |
| `legacy_attempted` | `legacy_attempted` (3) | `run` | Read the private receipt of the run, fix the cause it records, then rerun. |

`budget_exhausted` names the stage that consumed the budget in `exit_receipt.stage`
(`preparation`, `construction`, `trials` or `refinement`) when the run observed it.

## Where it is enforced

`agent_harness_lab/typed_exit.py` owns the vocabulary; `exit_guidance.py` reuses its
next steps as remediation. The regression catalog in
`tests/integration/cli/test_typed_exit_receipts.py` derives the exit paths from the
source of the entry point and fails when a non-zero exit lacks a typed reason.
