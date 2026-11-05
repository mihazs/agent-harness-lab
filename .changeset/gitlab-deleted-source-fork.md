---
"agent-harness-lab": patch
---

Keep GitLab merge requests whose source fork was deleted. GitLab sets `source_project_id` to null once the source project is deleted; the merge request is now recorded with `source_project_state: "unknown"` and a null `source_project_sha256` instead of failing the whole capture as an integrity error. An approver whose `user` object is missing or has no `id` is counted in `approvers_unidentified` instead of failing the capture, as documented; a `user` that is not an object, a non-positive ID or a boolean ID is still refused.
