---
"agent-harness-lab": patch
---

Fix #713: the evaluation-construction agent can no longer fail a run by writing its own grader-bundle or control `digest`. The construction prompt now tells the agent to omit every digest, a host-grader run drops authored digests right after the agent writes `manifest.json` (receipt: `authored_digest_ignored`, a count only) so they never reach the late integrity comparison, and a controlled run classifies an authored digest as the fixable `authored_digest_not_accepted` defect that uses the one bounded repair pass. The controller-computed digest stays the only frozen value, a grader or fixture changed after freezing still invalidates the run, and bank and replay validation still reject a wrong declared digest. The receipt records `evaluation_construction_seconds` and the `elapsed_seconds` of a rejected attempt.
