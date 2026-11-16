---
"agent-harness-lab": minor
---

Add `ahl bank-interchange convert` (part of #838): a reviewed `task-text-cases-json-v1` case list of tasks with text expectations (`contains`, `not_contains`, `names`, `flag`) becomes a native, frozen private task bank with a deterministic offline text grader and four control fixtures per case, so a baseline and a candidate are scored on identical inputs by the existing before/after guard. A case that needs repository or hosting-service context, carries an unsupported assertion kind or unknown field, has an invalid value, would accept an empty response or contradicts itself is excluded with a typed reason in `conversion.json`, never dropped, graded or counted as a pass. No grader is generated without the operator review attestation, permission and license declarations. The bank keeps independence unverified with one root per case and is not certification eligible.
