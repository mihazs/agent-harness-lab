---
"agent-harness-lab": patch
---

Make the synthetic sequential history tests deterministic under host load. The fixture's 0.25-second arm and 3-second root quotas, not the 30-second history budget, were what a loaded host exhausted: a real grader subprocess timed out, the controller stopped the root as incomplete, and tests then failed with a misleading downstream error. The fixture now uses generous synthetic quotas, reports why a run did not seal (controller stop reason, journal reason, dispatched roles, trial statuses, elapsed against budget), and keeps root-quota and history-budget exhaustion covered by explicit tests that advance an injected clock. Production deadline enforcement is unchanged.
