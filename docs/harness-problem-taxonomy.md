# Why the harness profile looks for these problems

This page explains which problems of agentic coding the [harness profile](project-profile.md)
detects, what public sources say about each, and which problems it leaves alone. Every
source is public research or vendor documentation. Nothing here comes from a private
project or session history.

## The problems, their signals and their fixes

| Problem | What the sources report | Signal in a checkout or in sessions | Harness fix | Detector |
| --- | --- | --- | --- | --- |
| Verification and run commands missing | Agents declare work done when they have no check to run; a CLAUDE.md should name the exact test, typecheck and lint commands and how to run one test ([Claude Code best practices](https://code.claude.com/docs/en/best-practices)). Build and Run appear in 63.0% and Testing in 75.9% of 332 sampled context files ([Agent READMEs](https://arxiv.org/html/2511.12884v2)). | a declared test, lint, typecheck or build command, or a CI step, that no instruction file names | name the command with its single-test form | `verification_command_unnamed` |
| Non-default toolchain not stated | A tool named in a context file was used 1.6 times per task, against fewer than 0.01 times when it was not ([Evaluating AGENTS.md](https://arxiv.org/html/2602.11988v3)). Practitioners attribute wrong package manager and wrong test flags to the repository rather than the model ([practitioner write-up](https://dev.to/devansh365/how-to-make-your-codebase-work-for-ai-coding-agents-without-better-prompts-kcb)); this is weaker evidence. | a pnpm, yarn, bun, uv, poetry, pipenv or pdm lockfile, or a `packageManager` field, with no package manager named | one line naming the toolchain | `toolchain_not_stated` |
| Stale lines: scripts, targets and paths the repository lacks | 23.0% of 356 repositories had stale code-element references ([Context Rot](https://arxiv.org/abs/2606.09090)). 90.1% of agent choices followed a stale file over the working code ([Xu and Wu](https://arxiv.org/abs/2610.30571)). A stale test command cost about a third more tool calls and no run repaired the file ([When Instruction Files Go Stale](https://arxiv.org/abs/2611.09802)). Drift was 48 of 118 logged failures in one case study ([Lessons Outlive Their Failures](https://arxiv.org/abs/2610.32578)). | a backticked `npm run x`, `make x`, `task x`, `just x` or repository path that does not exist | correct or remove the line | `command_target_missing`, `path_reference_missing` |
| Harness contradicts the toolchain | the same studies: the file outranks the code | the harness names npm where the lockfile is pnpm, or a test runner no dependency declares | align the line with the manifests | `toolchain_contradicted`, `test_runner_contradicted` |
| Instructions contradict each other | "If two instructions contradict each other, Claude may pick one arbitrarily" ([memory docs](https://code.claude.com/docs/en/memory)); Codex concatenates files root down and the nearest wins ([Codex AGENTS.md guide](https://learn.chatgpt.com/docs/agent-configuration/agents-md)). | two instruction files name different package managers | one source of truth | `harness_files_disagree` |

## Problems AHL already detects elsewhere

These have their own checks, so the profile does not duplicate them: oversized or vague
instruction files (the [static audit](static-audit.md) and the soft line budget), user
instructions and harness rules the agent ignored ([instruction adherence](instruction-adherence.md)),
repeated corrections and where users intervened (the brief's `user_reactions`), and repeated
tool failures (the brief's mechanisms). The sources for those: under 200 lines per file
([memory docs](https://code.claude.com/docs/en/memory)); developer constraint violation is the
most common misalignment, 38.33% of 16,118 episodes ([arXiv 2605.29442](https://arxiv.org/html/2605.29442v1)).

## Problems it leaves alone

- Non-functional requirements such as security (14.8% of sampled files) and performance
  (14.5%) are rarely specified ([Agent READMEs](https://arxiv.org/html/2511.12884v2)), but
  whether a project needs a rule is a judgement, not a repository fact.
- Directory tours and generated stack overviews. LLM-generated context files did not improve
  task success and raised cost by more than 20%, and overviews did not shorten the path to the
  changed files ([Evaluating AGENTS.md](https://arxiv.org/html/2602.11988v3)).

## Design rules this implies

1. **Only what the repository does not say plainly.** A gap finding names a fact an agent would
   otherwise guess (a non-default toolchain, an exact verification command), never a tour.
2. **A wrong line before a missing line.** Agents follow the file literally, so contradictions
   are listed before gaps.
3. **Sessions, not volume.** History findings count separate sessions, never occurrences or
   bytes, and report lineage as unknown. Unknown capture is `not_run`, never a clean result.
4. **History stays private.** A command key is a program from a closed vocabulary or a name the
   repository itself declares; arguments, paths and secrets never reach a count.
5. **A finding is a hypothesis.** The paired evaluation, the fresh confirmation and the install
   review decide whether a change is kept.
