---
"agent-harness-lab": minor
---

Add the model-free static harness audit library `agent_harness_lab.audit`. `audit.run(checkout, runtime)` reads the harness surface inventory at the pinned runtime version and reports one row per dimension (instructions, rules, hooks, workflows, agents, commands, plugins, config) as `conformant`, `nonconformant` or `unknown`, plus findings with fixed remediation labels ordered cheapest first. It reads only the facts the inventory parsers already recorded: nothing is executed, written, sent over the network or given to a model. Unread (`private_excluded`), unparsed, refused and unchecked surfaces stay `unknown` and never count as a pass, and a blocked or unsupported inventory leaves every dimension `unknown`. Each row is labelled a convention check, not performance evidence, and no aggregate score is emitted.
