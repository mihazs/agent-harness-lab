---
"agent-harness-lab": patch
---

Correct the one-call improvement guide: it no longer lists "unintegrated project banks" as a preflight stop or pinned target-bank integration as incomplete, and it now names the five reasons preflight returns for a pinned project bank that the run cannot use (`configured_bank_requires_cooperative_runtime`, `configured_bank_cannot_combine_with_managed_experiment`, `configured_managed_bank_requires_controlled_workflow`, `configured_bank_requires_development_regression_and_heldout`, `configured_bank_shared_trial_budget_exhausted`). Documentation only; no behavior change. Part of #142.
