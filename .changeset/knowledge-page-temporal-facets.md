---
"agent-harness-lab": minor
---

Add `sources.knowledge_facets`, an offline mapper from the declared `status`, `valid_from`, `valid_until`, `open_ended`, `supersedes` and `superseded_by` fields of knowledge-bundle pages to the temporal facets `temporal.resolve` takes. Knowledge time comes only from the caller, a page with an unreadable field is withheld with a finding instead of being guessed, and a supersession naming a page the bundle lacks keeps the declaring page unknown rather than in force. Part of #47.
