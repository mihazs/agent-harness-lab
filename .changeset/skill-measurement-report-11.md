---
"agent-harness-lab": minor
---

Add `ahl skills report --results RESULTS` (part of #11), a private `ahl-skill-measurement-report-v1` that rolls the skill fields saved evaluator trials already record into one report. It separates staged change (created, updated, removed, unchanged or unknown), availability, observed activation, same-name shadowing, natural versus preload routing and graded outcome per arm. A trial without usable skill fields, a receipt without its own evidence and a shadowing scan that does not cover every staged skill stay `unknown`, never `not_observed` or `none_found`; only `passed` and `failed` are outcomes. It never crosses activation with outcome and reports no utility estimate or success flag.
