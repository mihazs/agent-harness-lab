---
"agent-harness-lab": patch
---

Fix `ahl improve` ending as `invalid_input` with a bare `'digest'` after evaluation construction. The controller now freezes grader-bundle digests whenever bundles are declared and computes control-fixture digests from the frozen author output; an authored digest is rejected and never overrides it. Manifest and contract violations are typed reasons naming stage, file and field with the exact next step, an unexpected `KeyError`/`TypeError` is reported as `internal_contract_error`, the manifest shape is checked right after the author writes it, and one bounded repair pass feeds the typed reason back to the author and is recorded in the receipt.
