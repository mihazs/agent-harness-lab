---
"agent-harness-lab": patch
---

Add a deterministic simulation test-bed for the improvement loop under `tests/support/loop_sim.py`: a scripted evaluator, proposer, injected clock and trial counter, and a `Script` builder with twelve named presets (already accepted, converges, regresses, critical area, plateau, noisy tie, vacuous baseline and candidate for each of the five vacuity labels, budget exhausted, no mechanism, grader changed and interrupted). Each preset replays through the `agent_harness_lab.loop` state machine to one outcome and exactly one stop criterion, every vacuity label ends `evidence_insufficient` and never accepted, and two replays of a script and seed are byte-identical with no model call, subprocess, sleep, clock or network access. This is test support only: no product code, command, receipt or exit code changes. How to write a scenario is in `docs/loop-reference.md`.
