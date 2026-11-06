---
"agent-harness-lab": minor
---

Add a seeded Monte Carlo power simulation for frozen fixed-final experiment plans. `ahl experiment --spec PLAN --power-model MODEL [--simulations N] [--simulation-seed S]` draws synthetic trial records from a declared outcome model (baseline success, common or per-family effect, root spread, repeat correlation, critical-regression probability) and judges each set with the unchanged `statistics.analyze` engine. The private `fixed-root-power-simulation-v1` report gives disposition counts, rates and Wilson 95% Monte Carlo intervals, labels the supported rate as power or false-recommendation rate, and keeps roots and repeats separate. No worker or model runs, cohort-pinned and sequential plans are refused, and automatic promotion stays false.
