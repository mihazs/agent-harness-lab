---
"agent-harness-lab": minor
---

Add `--sessions FILE` to `ahl score`: an advisory, observed-only placement of recorded sessions into the built-in capability areas (coding, ux-ui, docs, review) beside the scored rows. A session is described by a content-free summary (an opaque id and counts of tool names, file extensions and command families); a fixed rule table places it, with no model call. A session with no recognized signal, fewer than three, a mixed signal (`ambiguous`) or a signal for an undeclared area stays `unclassified` with that reason and is never given the nearest area. The section never carries a `scored` state and never changes a row, an evidence state or the overall figure, so an area can show sessions while its row is `no_evidence`. Invalid summaries exit 2 with the position and never the value. Documented in `docs/score-by-capability-area.md` and `docs/score-reference.md`.
