---
"agent-harness-lab": minor
---

Add a controller-side relay from the supported hook to the isolated adapter worker (`evaluation.adapter_relay.Relay`). A failed tool call is answered with one fixed controller sentence naming a category the project declared before the run, never tool or adapter text, and the original output is delivered unchanged; a successful call gets the empty identity reply without starting the worker. A worker failure stops a required run and is recorded under an optional one, a redelivered call gets its first reply, and the metadata ledger holds the digest of every reply. The worker now reports a host that cannot create its private run directory as `sandbox_unavailable` instead of raising. No hook is installed, no command calls the relay, and effectiveness and live-runtime application are not claimed.
