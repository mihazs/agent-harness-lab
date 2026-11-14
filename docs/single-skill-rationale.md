# Why there is one skill

[Documentation index](README.md) · [Install and update](installation.md#moving-from-two-skills-to-one)

Earlier releases shipped `harness-evaluation` and `harness-self-improvement`. Asking an agent
to evaluate, fix, improve or maintain a harness could end at either, and each pointed at the
other for part of its procedure. They are now one skill, `harness-self-improvement`, whose
job is to run the single CLI command and report its real result.

## Decisions

- **One entrypoint, merged activation.** The description keeps every phrase that triggered
  either skill (improve, fix or maintain a harness; baseline/candidate evaluations; diagnosing
  evaluation failures; measuring supported improvements) and the exclusion of ordinary coding
  and application testing. The entry stays within the 200-line soft budget for always-loaded
  instructions; methodology that applies only sometimes lives in `references/`, the runner
  and trace miner in `scripts/`.
- **The surviving name is `harness-self-improvement`.** Existing installs, global links and
  `/harness-self-improvement` invocations keep working. The evaluation skill's references and
  scripts moved into it; its entry became `references/evaluation.md`.
- **The CLI owns the work.** The skill maps your words to `--scope` and runs the command once.
  It never starts a manual loop, repeats an invocation to get past a blocked, failed,
  exhausted or ambiguous result, or edits a global file by hand while the CLI path exists.
  The manual fallback remains only for a CLI that is unavailable before any attempt, with its
  verification limits stated.
- **Roles stay separated.** A controller-managed evaluator author still receives the
  evaluation methodology only, not the improvement entry, and a managed preparation stage
  never invokes the command.

## Migration

`ahl update`, the release installer and `ahl skills` back up a `harness-evaluation` link they
created and do not recreate it; a directory or a symlink you made yourself is left alone. See
[moving from two skills to one](installation.md#moving-from-two-skills-to-one).

## Limits

The routing tests are a lexical contract over the activation description. They show that no
former trigger phrase was dropped and that the exclusions remain; how a native agent routes a
real prompt needs a model run in that host and is not shown here.
