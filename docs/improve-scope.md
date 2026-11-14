# Improve the project, the user-level skills, or both

Use `--scope` when the harness you want to repair is not only the project's. The
default stays the project; the user-level layer is opt-in, per run or through a saved default, and named by you.

```sh
ahl improve . --repair-contracts                                          # project only (default)
ahl improve . --repair-contracts --scope global --agent claude           # your Claude Code user skills only
ahl improve . --repair-contracts --scope project+global --agent codex    # your Codex user skills, then the project
```

Both routes, `ahl improve` and `ahl skills invoke`, take the same option and return
the same receipt. To make a user-level scope the default of your own runs, see
[save a default scope](#save-a-default-scope).

## Choose what to repair

1. Decide the layer. `project` (the default) never reads or writes a user-level file.
   `global` repairs only the user-level skill directory of one runtime and leaves the
   project untouched. `project+global` does both in one run, one budget and one receipt.
2. For `global` or `project+global`, name the runtime with `--agent claude` or
   `--agent codex`. AHL never infers it from a project file, a remembered choice, an
   installed executable or an environment marker, because that would pick which of your
   personal directories to edit. Without it the command stops with exit 2 and tells
   you what to add.
3. Check that the location exists. The named location is the runtime's user skill
   directory:

   | `--agent` | User skill directory |
   | --- | --- |
   | `claude` | `$CLAUDE_CONFIG_DIR/skills`, or `~/.claude/skills` when that variable is unset |
   | `codex` | `~/.agents/skills` |

   A missing directory, a symlinked location, a location you cannot write and a
   location carrying an `.ahl-read-only` marker each stop the run with exit 2 before
   anything is written. AHL does not create the directory.
4. Run the command. In this release the user-level layer is changed only by
   `--repair-contracts`: the same deterministic, model-free skill-header repair the
   project gets. Without `--repair-contracts`, a user-level scope returns
   `status: blocked` with `reason: global_scope_model_evaluation_unavailable`, exit 3,
   and nothing is written or evaluated.

## Save a default scope

A user-level default is optional, per user and never part of a project. It lets runs
that name no `--scope` use `global` or `project+global` without repeating the flags.

```sh
ahl config set-scope project+global --agent claude   # runs without --scope now use it
ahl config show                                      # default_scope: path, scope, agent, reason
ahl improve . --scope project                        # one run that ignores the saved default
ahl config clear-scope                               # back to the project default
```

1. Run `ahl config set-scope SCOPE --agent codex|claude`. Only `global` and
   `project+global` can be saved, because `project` is already the default, and the
   runtime is required: AHL never picks which of your personal directories to edit.
2. Run `ahl improve` as usual. Without `--scope` the saved value is used, with the saved
   runtime unless the run passes its own `--agent`. An explicit `--scope` on the command
   line, `project` included, always wins for that run.
3. Read `scope_source` in the receipt: `user_configuration` when the saved default chose
   the scope, `command_line` when `--scope` did and `default` when nothing did.
4. Expect `blocked` without `--repair-contracts`. A user-level layer is still changed
   only by the deterministic skill-header repair, so a saved default makes a plain
   `ahl improve .` stop with `global_scope_model_evaluation_unavailable`, exit 3, and
   write nothing. Its `remediation` names both ways out: `--scope project` for one run
   and `ahl config clear-scope` for good.

The file is `user-scope.json` beside the remembered-runtime file (see
[remember the runtime choice](one-call-improvement.md#remember-the-runtime-choice) for
its location and safety rules) and has the same limits: owner-only, regular, at most
1 KiB and never read from or written to a project. A corrupt, oversized, non-regular or
not owner-only file is ignored and the run stays on the project; `ahl config show` names
the `reason`. `ahl config clear` forgets the remembered runtime only.

## Read the receipt

Every scope returns `scope`, `layers`, `layers_evaluated`, `layers_changed`,
`changed_files`, `recommended_files` and `blocked`; see the
[scope receipt reference](one-call-improvement.md#harness-scope-receipt). A user-level
file in `changed_files` carries a `global:` prefix, and each row in `layers` names its
own plan, deployment and rollback command. A blocked, inconclusive, exhausted or failed
layer is reported as such and gives the run that status and exit code; it is never
folded into a success.

## Undo a user-level repair

Use the rollback command printed in that layer's row. It names the user-level
directory as the project:

```sh
ahl rollback DEPLOYMENT_ID --project ~/.claude
```

Rollback restores the saved bytes and modes, or reports a conflict when you edited the
file since; it never overwrites your edit.

## What stays out of reach

- A project `ahl.json` cannot select a scope. A `scope` key in it is rejected with
  exit 2 before anything is created; only the command line or your own saved default
  can add the user-level layer.
  The file still limits the project layer (its `harness_paths` and its recommendation-only
  rule) and the run's time budget, and it never limits or widens the user-level layer.
- Instruction files, settings, hooks, permissions, secrets, credentials, memory,
  managed locations and any bundle that is a symlink are never read for editing or
  written, and only each real bundle's `SKILL.md` header is opened.
- The default scope refuses a project directory that is, or sits inside, a user-level
  location (for example `ahl improve ~/.claude`). Use `--scope global` to edit that location on purpose.
- Applying a repair never claims certification: behavior-changing results keep their
  existing independent-certification gate.
