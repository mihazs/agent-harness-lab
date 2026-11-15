---
"agent-harness-lab": minor
---

Show a supplied precision-feasibility preflight in the `ahl compare` page. An experiment record may now carry a `precision_preflight` (the `fixed-root-precision-v1` result): the page adds a "Precision-feasibility preflight" section with the distinct task, root and repeat counts, the trial ceiling, the half-width target, per-endpoint precision, the solver's sample-size plan and a "Why not feasible" row built only from the supplied flags. Nothing is recomputed or coloured, an unknown value is never zero, a malformed or self-contradicting block or one that claims promotion, budget or publication authority refuses the input, and a public export refuses it.
