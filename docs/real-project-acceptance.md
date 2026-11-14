# Validate AHL on a real project

Use this when you want to know whether `ahl improve` gives an honest, useful result on a repository you must not change:
either an evidenced candidate or a typed reason that names the next step, a before/after evaluation on identical inputs, no
number the recorded trials do not support, and a second run that knows about the first. It runs each step through the
[isolated project check](isolated-project-check.md), so the real checkout is fingerprinted before and after and a temporary
worktree does all the work. A synthetic project runs the same steps in CI ([below](#run-the-synthetic-equivalent)).

Nothing here is published. The results are private field validation of one repository; the repository only ever gains the
synthetic equivalent.

## Before you start

- Read what the isolation [does and does not cover](isolated-project-check.md#what-it-does-not-undo). A runtime still writes its
  own session logs under your home directory ([delete them below](#delete-the-private-outputs)).
- Rehearse the procedure on the synthetic project first: `task test:isolated` (no `REPO`). A failure there is a defect in AHL or
  in this page, not in your project.
- The repository needs recorded sessions of the runtime you will use, under the path of its main checkout. The temporary worktree
  has another path, so every step reads them with `--history-from`
  ([why](improve-from-past-sessions.md#try-it-in-a-worktree)).
- Pick a model budget. Each step below takes `--timeout SECONDS`; the examples use 900.

## Keep the private outputs in one place

Everything the protocol produces describes a real project. Put it under one directory outside every repository, readable by you
only, and give the run directories of AHL the same home so they survive for review:

```sh
umask 077
PRIVATE=$(mktemp -d "${TMPDIR:-/tmp}/ahl-acceptance-XXXXXX")
export AHL_STATE_HOME="$PRIVATE/state"
```

The steps pass `AHL_STATE_HOME` through to the command (`--pass-env`); without it the check keeps the run directory in its own
temporary folder and deletes it with the worktree. Do not commit, paste or attach anything from `$PRIVATE`.

## Run the steps

Each step is one isolated check. Everything before the second `--` configures the check and everything after it is the command;
`{worktree}` is replaced by the temporary worktree. `--inherit-home` lets the runtime use its saved login and read its
recorded sessions. Replace `/path/to/project` and the agent.

<a id="first-use"></a>

### Step 1: first use with history

```sh
task test:isolated REPO=/path/to/project -- --ref HEAD --timeout 1800 --memory-cap-gib 5 --inherit-home \
  --pass-env AHL_STATE_HOME --receipt "$PRIVATE/first-use.isolated.json" --log-dir "$PRIVATE/first-use" -- \
  python -m agent_harness_lab improve '{worktree}' --history --history-from /path/to/project --agent claude --timeout 900
```

The `improve` receipt is `$PRIVATE/first-use/stdout.txt`. With usable history (`history.events` above zero) it passes when it
holds an evidenced candidate (a `plan`) or a typed `reason` with a `remediation`; it fails when it ends as a bare `no_change`:

- PASS: `plan` is present, or `status: blocked` (or `no_change`) carries both `reason` and `remediation`. When
  `analysis.findings` is above zero, `findings_not_candidates` says why none became a candidate; when the run spent model time
  and evaluated nothing, `spend_label` is `spent_without_evaluation`.
- FAIL: `no_change` or any other end without a `reason` or without a `remediation`, findings that are never explained, or
  spent budget without the label.

A candidate needs evidence the history may not hold yet. If the run ends with `no_outcome_evidence`, follow its `next_steps`; to
see the evaluation machinery run anyway, add `--focus 'a hypothesis you want tested'` ([hypothesis probes](hypothesis-probes.md)).

<a id="before-after"></a>

### Step 2: before and after on the same inputs

A receipt with a candidate (step 1 with `--focus`, or any run that reached evaluation) must show both arms scored on one frozen
set of inputs. Read `decision.bindings` and `decision.observed`:

- PASS: `manifest_sha256` and `results_sha256` are present, `baseline_sha256` differs from `candidate_sha256`,
  `observed.trials` is twice `observed.tasks`, and `observed.pairs.unknown` is counted apart from the passes and failures.
- FAIL: equal arm digests (that is the `arms_identical` [label](vacuity-labels.md)), a missing frozen-input hash, or a
  candidate that scored worse (`pairs.baseline_only_passed` above `pairs.candidate_only_passed`) yet still carries a `plan` or a
  `recommendation`.
- KNOWN_GAP: a worse candidate that ends without the typed `candidate_regressed` block and its investigation record (slice
  FU-2, #804), and a project whose own case list cannot be turned into an executable bank (#838). Record the gap; do not call
  it a pass.

<a id="no-fake-results"></a>

### Step 3: no fake results

Apply this to the receipt of every step, not as a run of its own.

- A receipt with a `vacuity` block (`vacuous: true`) must end `inconclusive` with `reason: evaluation_vacuous`, no `plan` and no
  `recommendation`. Any success wording on a vacuous evaluation is FAIL.
- Every number must trace to a recorded trial. `decision.bindings.evaluation` names the evaluation folder under
  `$AHL_STATE_HOME`; `observed.trials` must equal the number of rows in `trials` of its `results.json`, and
  `sha256sum results.json` must equal `results_sha256`. A count you cannot trace is FAIL; a missing file is KNOWN_GAP.

<a id="incremental"></a>

### Step 4: incremental runs

Run step 1 again after an edit between the runs. The edit never touches the project: make it in a scratch copy, save it as a
patch, and let the check apply it inside the temporary worktree.

```sh
git -C /path/to/scratch-copy diff > "$PRIVATE/edit.patch"
task test:isolated REPO=/path/to/project -- --ref HEAD --timeout 1800 --inherit-home --pass-env AHL_STATE_HOME \
  --patch "$PRIVATE/edit.patch" --receipt "$PRIVATE/second.isolated.json" --log-dir "$PRIVATE/second" -- \
  python -m agent_harness_lab improve '{worktree}' --history --history-from /path/to/project --agent claude --timeout 900
```

- PASS: the edit reached the second baseline (`baseline_sha256` differs from the first run's), and the second receipt names the
  first run (applied, ignored or regressed), reports the delta and does not repeat finished work.
- FAIL: the second run spends the same preparation again and says nothing about the first. A release without the incremental
  slice (FU-4, #806) fails here by construction: record FAIL with that issue, never PASS.

<a id="stress"></a>

### Step 5: stress scenarios

Record one result per cell you can induce through the same check; a cell you cannot induce is `SKIPPED`. The typed outcome is
the criterion: any non-zero end without a `status` and a `reason` is FAIL.

| Scenario | How to induce it | Typed outcome expected |
| --- | --- | --- |
| No history for the worktree | drop `--history-from` | `blocked`, `history_not_found_for_worktree` |
| Runtime missing | run with a `PATH` that lacks the runtime | `blocked`, `agent_runtime_unavailable` |
| Runtime newer than the verified range | none needed on a current runtime | `capture_unverified` among `findings_not_candidates` causes, never a clean pass |
| Time budget too small | `--timeout 5` on the command | a typed `budget_exhausted` or `blocked`, not `execution_error` |
| Concurrent runs | start two checks together | both end typed; neither changes the checkout |
| Interrupted run | stop the check with Ctrl-C | the worktree is gone (`git worktree list`) and the checkout is unchanged |

The scenario matrix with failure injection is slice FU-5 (#807); until it lands, the cells above are the manual subset.

## Record the result

Write one row per step in `$PRIVATE/result.md`: the step id, `PASS`, `FAIL`, `KNOWN_GAP` or `SKIPPED`, one line saying why, and
the receipt file names. `KNOWN_GAP` means the receipt lacks the field that would decide the criterion or the capability is open
work; a missing or unknown value is never read as `PASS`.

| Step id | Criterion |
| --- | --- |
| `first-use` | an evidenced candidate, or a typed reason and remediation |
| `before-after` | both arms on one frozen input set; a worse candidate gets no plan |
| `no-fake-results` | vacuous evaluations are labelled and never improved; every count traces to a trial |
| `incremental` | the edit reaches the second baseline; the second run reports the first |
| `stress` | every induced scenario ends typed |

What may leave `$PRIVATE`: the step id, the result word and the number of a tracking issue. Never a project name, path, session
identifier, count or figure; keep the receipts private because they describe a real project.

## Delete the private outputs

```sh
rm -rf "$PRIVATE"; unset AHL_STATE_HOME
git -C /path/to/project worktree list    # one line: the checkout itself
git -C /path/to/project worktree prune
```

The check already removed its temporary worktree and its own temporary state. The runtime may also have recorded the run under
your home directory, outside the repository and outside the isolation. The temporary worktree path contains `ahl-isolated-`,
so list the candidates, check each one is a run of this protocol, and delete exactly those paths:

```sh
ls -d ~/.claude/projects/*ahl-isolated-* 2>/dev/null
grep -rl 'ahl-isolated-' ~/.codex/sessions 2>/dev/null
```

A memory tool may have recorded the session as well; remove that record with the tool itself.

## Run the synthetic equivalent

`task test:isolated` without `REPO` runs `tests/integration/tools/test_acceptance_protocol.py`: a synthetic repository with three
failing recorded sessions, a stub runtime and the same check around `ahl improve`, with the options used above. The default
`task test` runs it too, so CI covers the procedure on every change.

| Step id | Synthetic test |
| --- | --- |
| `first-use` | `test_first_use_with_recorded_history_ends_with_a_typed_reason_and_a_remediation_and_leaves_the_checkout_unchanged`, `test_first_use_with_a_hypothesis_ends_with_an_evidenced_candidate_that_is_never_called_improved` |
| `before-after` | `test_before_and_after_are_scored_on_one_frozen_set_of_inputs_and_two_different_arms`, `test_a_worse_candidate_carries_no_plan_and_no_recommendation` |
| `no-fake-results` | `test_a_vacuous_evaluation_is_labelled_and_never_reported_as_an_improvement`, `test_every_reported_trial_count_traces_to_the_recorded_results_file` |
| `incremental` | `test_an_edit_between_two_runs_reaches_the_second_baseline_and_never_the_real_checkout` |
| `stress` | `test_a_worktree_without_its_own_history_ends_typed_when_the_main_checkout_is_not_named`, `test_a_missing_runtime_ends_typed_and_spends_nothing` |
| private outputs | `test_every_private_output_sits_under_one_directory_and_deleting_it_leaves_nothing_behind` |

The synthetic project covers the procedure and the criteria a current release meets. The two things it cannot cover yet are
marked `KNOWN_GAP` and `FAIL` above (typed `candidate_regressed`, a second run that reports the first); they join the synthetic
project when FU-2 (#804) and FU-4 (#806) ship.
