---
name: harness-evaluation
description: Build and run baseline/candidate evaluations for agent instructions and skills, diagnose evaluation failures, and measure supported improvements. Use for harness maintenance, not ordinary application testing.
---

# Harness evaluation

Evaluation is an executable part of harness improvement. Construct missing tests,
run them, inspect outcomes, and repair the evaluation when necessary. Do not require
an existing benchmark, perfect labels, complete history, or statistical significance
before doing a useful bounded comparison. Never turn limited evidence into a broader
claim than it supports.

## Choose evidence that answers the claim

| Evidence | Establishes | Does not establish |
| --- | --- | --- |
| Schema, references, source/contract checks | Structural validity and verified contradiction removal | Model behavior or task success |
| Read-only decision probe | Decisions on the supplied scenario | Execution, deployment, or actual side effects |
| Task execution and artifact grader | Observed completion under the recorded conditions | General reliability or production acceptance |
| Blinded rubric over actual outputs | Qualified comparative quality | Objective correctness or independent replication |

Prefer external executable checks of final state. Model judgment supplements them;
require concrete artifact/trace citations and hide version labels from the judge.
Do not grade success from the agent's own final claim or source-word matching.

## Diagnose traces and choose informative tasks

For multi-session diagnosis or recurring failures, read
[trace diagnosis and method selection](references/trace-diagnosis.md). Use process
mining to discover sequences and case-supported patterns, then check raw evidence
and current harness contracts. Patterns propose mechanisms; they do not establish
causation or authorize a change. Use ML only when it answers a named question
better than simple counts and a reviewed sample.

Cover distinct material mechanisms, not just several variants of the first one.
Freeze an observable acceptance boundary for each selected mechanism. Include a
partial repair that the grader must reject, a successful control and a relevant
safety regression. Preserve a held-out task family or time period when testing
transfer; random event splits can leak the same session into both sets.

If baseline and candidate both pass, distinguish a redundant rule from an
insensitive test: did the baseline exercise the suspected failure, do ambient
instructions already supply the fix, and would a blocker-only or superficial
repair pass the grader? Strengthen one test from the actual failure mechanism
without encoding the solution. A tie closes that candidate, not unrelated
mechanisms. Do not spend the entire budget repeatedly confirming easy passes.

## Construct and run

1. Inspect available runtime, authentication status, existing tests and fixtures.
   Reuse installed tools. The bundled [runner](scripts/run_evaluation.py) supports
   Codex and Claude Code JSONL trials; read [the execution contract](references/execution.md) to use it.
2. Freeze task inputs and graders before scoring. Start with the observed failure,
   an adjacent regression, and an unseen transfer case; separate development and
   held-out feedback. Synthetic cases must represent the failure mechanism, not
   contain its solution. A historical approximation is labeled as such.
3. Snapshot baseline/candidate files and record hashes, model, reasoning settings,
   tool access, ambient instructions and resource limits. Keep conditions matched
   within each pair; record unavailable controls. Never pool different models as
   comparable repeats. Do not change model or permissions to chase a better score.
4. Use fresh isolated fixture copies for each trial. Keep candidate instructions out
   of baseline, graders and reference answers outside writable task workspaces, and
   evaluation artifacts outside skill discovery. Verify the intended version is
   loaded. Worktrees, if needed, belong under `~/dev/.worktrees/`.
5. Run baseline, then candidate on identical inputs; preserve negative results.
   Inspect artifacts and traces, not just exit codes. Repeat the decisive behavioral
   difference in fresh sessions. Reserve unseen tasks until candidate selection;
   if their feedback informs another edit, use new held-out cases.
6. Assess success and critical regressions first. Record time, tool calls and token
   usage only when observed. Compare efficiency with matched task and feedback
   budgets; extra retries do not prove a better harness. If both pass, do not claim
   a success-rate gain. Test a harder relevant case or retain the simpler version.

Default shared budget: 24 agent trials and 45 minutes including recovery and nested
trials. Within it, allow at most three candidate variants and one refinement per
mechanism; do not stop a multi-mechanism audit after three total fixes. Allocate
coverage before repetitions and reserve about a quarter for final checks. These
ceilings are not targets. Use user-specified limits when supplied.

## Recover from evaluation failures

Use this order within the authorized scope; do not ask the user to design the eval:

1. Diagnose the failed command or missing artifact. Distinguish task failure from
   runner/setup failure, grader defects, timeout, unavailable access and invalid data.
2. Repair local setup and retry a transient failure once. An unrelated connector
   startup warning does not invalidate an otherwise completed trial.
3. Use another already-authorized runtime when available; rerun both sides under
   that runtime. Never inherit candidate conclusions into a supposedly fresh baseline.
4. Construct a smaller executable reproduction with controlled fixtures and an
   observable oracle. Missing production access narrows claims to local behavior.
5. For decision-only claims, use matched decision probes. For qualitative outputs,
   compare actual executed outputs with a frozen blinded rubric and report uncertainty.

No usable runtime at all is a real execution error, not evidence of success. Continue
independently checkable corrections and report the exact failed capability and recovery
attempts. Missing history, a large benchmark, perfect controls, or labels alone must
never terminate the improvement workflow.

Version a repaired grader and rerun both sides. Do not let the candidate edit graders,
reference answers or thresholds. Missing completion events, malformed traces, timeouts,
and missing results cannot pass. A passing process with a failed acceptance check is
still a task failure.

## Decide and report

Recommend adoption only for the claim supported by the checks: targeted behavioral
benefit with relevant regressions passing, or a directly verified contract correction.
Reject critical correctness or safety regressions. Retain the simpler baseline when
no benefit is demonstrated. Structural checks alone do not support behavioral claims.

Report versions, task roles, actual outcomes, failed/not-executed checks, sample size,
conditions, observed costs and limitations. Report tested, rejected, already-covered
and deferred mechanisms separately; data volume is not a coverage measure.
Keep sanitized traces and rollback artifacts;
do not create routine ai-memory pages. Research informs experiment design, not local
proof: see [evidence and limits](references/evidence-to-design.md).
