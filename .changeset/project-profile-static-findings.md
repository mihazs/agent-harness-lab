---
"agent-harness-lab": minor
---

`ahl audit` adds a read-only project profile (technologies, package managers, declared and CI commands, layout) and compares it with the instruction files. It lists harness lines the repository contradicts (a script, target or path that is not there, a contradicted package manager or test runner, instruction files that disagree) and facts it declares that the harness never states (a non-default toolchain, a verification command), each with a stable id, counted evidence and a proposed update. Nothing is executed or installed; the findings are hypotheses for `ahl improve`.
