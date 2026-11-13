---
"agent-harness-lab": minor
---

Add an offline `sources.ci_leads.merged_pull_failing_ci_leads` that lists merged pull requests whose exact head commit still has failing check runs, workflow runs or newest commit statuses in a GitHub capture. Each lead is a scoped investigation lead with no verdict, failure timing against the merge, explicit alternative explanations, and unevaluated closed-unmerged PRs, plain issues and missing CI kept visible (part of #58).
