# Project profile and harness findings

The project profile is a deterministic, read-only description of a checkout. The harness
findings compare it with the instruction files and list lines the repository contradicts and
facts it declares that the harness never states. No model is called, nothing is executed or
written, and the checkout is only read. For why these problems and not others, see
[the problem taxonomy](harness-problem-taxonomy.md).

`ahl audit PROJECT` prints the findings after the convention checks and keeps the full
profile under `harness_profile` in the private JSON report. The section sits outside
`audit_sha256`, like the other separate observations.

## What the profile reads

Only manifests, lockfiles, task-runner files and CI configs, through a bounded no-follow reader
(1 MiB per file, three directory levels, never `node_modules` or other generated folders):

- Technologies and toolchain: `pyproject.toml`, requirements files, `package.json`
  (including `packageManager`), lockfiles, `Cargo.toml`, `go.mod`, Dockerfiles, task runners and
  CI. Package managers come from lockfiles, the `packageManager` field and tool tables.
- Declared commands: `package.json` scripts, `Makefile` targets, `Taskfile` tasks and `justfile`
  recipes, each with a category (`test`, `lint`, `typecheck`, `format`, `build`, `run`).
- CI commands: the `run:` steps of `.github/workflows/*.yml` and the script lines of
  `.gitlab-ci.yml` that work in a known category.
- Layout: source and test folders, monorepo tools and migration folders.

A fact the reader cannot settle (an unparsable or oversized manifest, a Makefile with
`include` or pattern rules, a Taskfile with `includes`) is recorded under `unknown` or
`incomplete`, and the findings that depend on it stay silent.

## Findings

Each finding has a stable `id` (`hp/<kind>/<digest>`, independent of line numbers), a
`finding_class` (`contradiction` or `gap`), a `subject`, counted `evidence`, up to three
`where` locations (instruction file and line) and a `proposed_update`. `status` is always
`candidate_claim`: a hypothesis, never a diagnosis.

| Kind | Class | Fires when | Proposed update |
| --- | --- | --- | --- |
| `command_target_missing` | contradiction | a code span runs `npm run x`, `pnpm x`, `yarn x`, `make x`, `task x` or `just x` and the root runner file is read, complete and declares no `x` | `correct_or_remove_line` |
| `path_reference_missing` | contradiction | a code span or Markdown link names a repository path that exists neither at the root nor beside the file | `correct_or_remove_line` |
| `toolchain_contradicted` | contradiction | the harness runs a package manager of an ecosystem and names none that the lockfile or `packageManager` field shows | `correct_or_remove_line` |
| `test_runner_contradicted` | contradiction | the harness runs `jest`, `vitest`, `mocha` or `ava` and the dependencies declare another of them but not that one | `correct_or_remove_line` |
| `harness_files_disagree` | contradiction | two root instruction files name disjoint package managers | `resolve_disagreement` |
| `toolchain_not_stated` | gap | a non-default package manager is detected and no instruction or skill file names any of the ecosystem | `state_toolchain` |
| `verification_command_unnamed` | gap | a category of declared or CI command (`test`, `lint`, `typecheck`, `build`) is named by no instruction or skill file, by command or by tool | `name_verification_command`, with up to three candidate command lines |

A line is not a claim, and stays silent, when it negates ("never", "instead of", "deprecated"),
gives an example ("for example", "such as"), follows a `cd`, passes an option before the name
(`pnpm --filter x run y`), holds a placeholder or glob, names a generated or git-ignored
folder, or is a bare `pnpm x` for a name that could be a binary. No finding is produced when
no instruction file could be read: the section says `no_instruction_file`, which is not a clean
result. When a candidate instruction file could not be read, the gap checks stay silent.

## In `ahl analyze` and `ahl improve`

Both commands build the same profile and write it to the private `harness-profile.json` of the run
directory. The analysis brief gains a bounded `harness_profile` section (finding ids, kinds, subjects,
counts and proposed actions, at most 12 findings, contradictions first) and names the private file in its
`sources`; `report.md` lists the findings. The proposer is told, when the section has findings, that they are
hypotheses, that a contradiction outranks a gap, and that it may add only a fact an agent cannot read from
the repository (a non-default toolchain, an exact verification command), never a directory tour. A mechanism
may cite a finding id in `evidence_refs`; an id the brief did not list is refused like any other unlisted
evidence. The evaluation author then builds tasks that try to reproduce the claimed weak outcome under the
baseline, and nothing installs without the paired evaluation, the fresh confirmation and the install review.

A first run whose history has findings but no verified outcome seeds the proposer with the best static
convention (see [one-call improvement](one-call-improvement.md)); a profile finding is one of those
conventions, ranked after a truncated chain and an oversized file for a contradiction, and last for a gap.
The seed carries a fixed focus sentence and the finding id, never instruction text.

## Limits

The checks are lexical. They do not execute a command, resolve an import, read a nested
package's scripts for a root claim, or prove that a harness line is loaded. A finding says a
line looks wrong or a fact looks missing; whether changing the harness helps is decided only by
the paired evaluation, the fresh confirmation and the install review that `ahl improve` already
runs. Findings are never installed by `ahl audit`.
