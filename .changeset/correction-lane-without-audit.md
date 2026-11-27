---
"agent-harness-lab": patch
---

The verified-correction lane also runs for a plain `ahl improve` (without `--from-audit`): an installing run whose candidate corrects current static contradictions of the project, and passes every gate, installs that correction instead of ending `no_change`. A run whose candidates correct no contradiction is unchanged.
