---
"agent-harness-lab": minor
---

Show the frozen preservation cohorts in the static `ahl compare` review page. When the supplied analysis carries a `preservation` block, the page adds a "Preservation cohorts" section with the paired transition cells of all frozen pairs and of each cohort, each cohort's role, margin, disposition and interval with its independent roots, the trial coverage counts, the error allocation and the stated assumptions, all copied from the analysis and never recomputed. A protected loss stays visible beside an aggregate gain, an interval that was not computed shows `unknown` instead of zero, an incomplete analysis shows the possible outcomes with missing trials as not imputed, and a malformed block is shown as unknown or skipped. A page without the block is unchanged.
