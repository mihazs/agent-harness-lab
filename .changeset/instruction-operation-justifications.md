---
"agent-harness-lab": minor
---

Let an `instruction_plan` justify each changed instruction or rule file with an `operations` list (#79). Each entry cites a mechanism id and evidence labels, states the expected change, the required behaviours it preserves and a falsifier, and declares one of `add`, `update`, `remove` or `consolidate`. Once a plan lists any entry, `instruction_validation.validate` blocks an unjustified change, an entry for an unchanged path, and a declared decision that differs from the computed one, and the report gains a `decisions` list that keeps text only as hashes. A plan without `operations` is unchanged. A justification is a declaration and grants no adoption authority.
