# Improve the project, the user-level skills, or both

Use `--scope` when the harness you want to repair is not only the project's. The
default stays the project; the user-level layer is opt-in, per run, and named by you.

```sh
ahl improve . --repair-contracts                                          # project only (default)
ahl improve . --repair-contracts --scope global --agent claude           # your Claude Code user skills only
ahl improve . --repair-contracts --scope project+global --agent codex    # your Codex user skills, then the project
```

Both routes, `ahl improve` and `ahl skills invoke`, take the same option and return
the same receipt.

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
  exit 2 before anything is created; only the command line can add the user-level layer.
  The file still limits the project layer (its `harness_paths` and its recommendation-only
  rule) and the run's time budget, and it never limits or widens the user-level layer.
- Instruction files, settings, hooks, permissions, secrets, credentials, memory,
  managed locations and any bundle that is a symlink are never read for editing or
  written, and only each real bundle's `SKILL.md` header is opened.
- The default scope refuses a project directory that is, or sits inside, a user-level
  location (for example `ahl improve ~/.claude`). Use `--scope global` to edit that location on purpose.
- Applying a repair never claims certification: behavior-changing results keep their
  existing independent-certification gate.
