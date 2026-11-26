---
"agent-harness-lab": minor
---

The harness profile now flags a runtime version a root instruction file states (`Python 3.9`, `Node 16`, `Go 1.20`, `Rust 1.70`) below the minimum, or other than the exact pin, that `requires-python`, `engines.node`, `rust-version`, the `go` directive, `.python-version`, `.nvmrc` or `.node-version` declare, as a `version_contradicted` finding in `ahl audit`, the analysis brief and the proposer's citable claims.
