---
"agent-harness-lab": minor
---

Add `evaluation.adapters.AdapterSpec`, an opt-in declarative capability contract for one presentation adapter (`ahl.adapter-spec.v1`), and `review_candidate_spec`, a non-executing check of an untrusted candidate against the controller-owned spec and project authorization. A spec that declares side effects or any filesystem, network, credential or nested-tool grant is rejected, and so are limits above the fixed ceilings and unknown or missing fields. A candidate that adds a tool, transform or grant, raises a limit, renames the adapter, or changes its contracts or runtime is rejected. New implementation bytes need a `previous_digest` equal to the authorized spec's `content_digest` (the implementation SHA-256, not the spec digest), and unchanged bytes must keep the authorized `previous_digest`. The review never runs adapter code or promotes, and it reports runtime integration as unsupported. Nothing calls these APIs by default.
