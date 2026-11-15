---
"agent-harness-lab": minor
---

Add append-only revision chains to the prediction ledger. A changed prediction is registered as a new record that names the one it `supersedes`; the chain is linear and stays on one mechanism, an unregistered, already-revised or unchanged parent is rejected, and the revision inherits no outcome or preregistration. The superseded prediction stays registered, reconciled and listed in the private mechanism coverage report with its revision number, `superseded_by` and a `superseded_predictions` count, so a contradicted prediction cannot be hidden by revising it.
