---
"agent-harness-lab": minor
---

Name the documented winner when a skill exists in both the user and project layers of `ahl improve --scope project+global`. The receipt's `precedence` now carries one `shared_skills` row per shared name with its `resolution`, `effective_layer`, `shadowed_layer`, `project_locations` and the documentation `source`: for `claude` the personal skill runs over a project skill in `.claude/skills`; for `codex` both skills stay available. A project copy outside the runtime's own project skill folder, or a user copy outside its user skill directory, keeps the winner `unknown`, and `precedence.same_name_skills` is now `none_shared`, `documented_rule` or `effective_layer_unknown` (it was always `effective_layer_unknown`). Attribution only: each file is still repaired in the layer that holds it. Part of #653.
