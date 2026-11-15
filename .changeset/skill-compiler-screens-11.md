---
"agent-harness-lab": minor
---

Harden the default-off experience-to-skill hypothesis compiler `evaluation.skills.compile_procedure` (part of #11). It now rejects input that is not an object, undeclared fields (held-out tasks and grader feedback cannot reach it), non-text procedure fields, credential-shaped text (including environment-variable assignments such as `DB_PASSWORD=`), text that copies a caller-supplied held-out answer as a whole word, also when it spans lines, entries or fields (an answer too short to screen is rejected, not skipped), repeated entries and lines the target's existing instructions already hold, and records over a caller-set `max_bytes` (at most 16 KiB). The hypothesis reports which screens ran: a list that was not supplied is `not_run`, never a pass. It still installs nothing and needs an independent paired evaluation.
