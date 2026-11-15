---
"agent-harness-lab": patch
---

A loop declaration that names a key `ahl.json` should not (an unknown field, or an area that is not declared in a per-area `accept_score` map) no longer repeats that key in the `loop_field_unknown`, `loop_authority_refused` and `loop_area_undeclared` errors unless it is a plain identifier of at most 64 characters. Any other key, including one with an escape sequence, a newline or another control character, is reported as `<unknown>`, so a hostile project file cannot clear or recolor the terminal or forge an `ahl:` line through the message. Part of #810.
