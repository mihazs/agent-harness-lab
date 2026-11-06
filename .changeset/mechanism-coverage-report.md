---
"agent-harness-lab": minor
---

Add `diagnostics.coverage.report` and `render_markdown`, a private `mechanism-coverage-v1` JSON and escaped Markdown report over mechanism records or `run_queue` output. Each mechanism keeps its responsible component, claim level (`observed_pattern`, `supported_diagnosis`, `development_recommendation`, `validated_improvement`), disposition and coverage state separate. A record without captured evidence is `unknown_capture` whatever its disposition, a disposition the record cannot support stays open, and complete coverage requires every mechanism resolved plus an explicit complete-capture declaration. Routes and claim levels are reported as hypotheses, not causal proof.
