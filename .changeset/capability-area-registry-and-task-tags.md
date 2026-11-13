---
"agent-harness-lab": minor
---

Add capability areas (part of #785): a project declares `areas` in `ahl.json`, bank tasks may carry one to three `areas` tags, and every declaration defect is a typed project-declaration reason (`area_registry_invalid`, `area_duplicate`, `task_areas_empty`, `task_areas_too_many`, `task_areas_invalid`, `task_area_undeclared`) that names the file and field. `ahl bank` lists a per-area task count, and `ahl improve` and configured evaluations reject a tag the project does not declare before any trial. Existing banks and configurations are unchanged. No scoring yet.
