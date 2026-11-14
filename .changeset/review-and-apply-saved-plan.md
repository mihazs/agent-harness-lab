---
"agent-harness-lab": minor
---

Add `ahl apply RUN [--project DIR] [--authorize-plan SHA256]`, a public review and application step for the change plan saved by an improvement run. Without an authorization it prints a read-only review packet with operation-level hashes, modes and line counts, binding checks against the project, configuration, policy, baseline and candidate, and the run's declared evaluation. With the exact plan hash it applies only a skill-header repair that it recomputes from the staged bytes, through the rollback journal and the target-state receipt. A changed input makes the plan stale, behavioral candidates stay blocked, and no approval or saved report becomes certification.
