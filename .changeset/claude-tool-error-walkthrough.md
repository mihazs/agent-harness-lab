---
"agent-harness-lab": patch
---

Add a synthetic native-capture walkthrough to the telemetry contract. A committed four-row `claude-jsonl-v1` fixture shows what normalization keeps and what it leaves out: bodies are omitted, a tool error stays `execution_status: error` with an unknown outcome, a missing result becomes `unmatched_call` coverage, and state and input/result fingerprints stay absent. The page says normalization is not general sanitization and that compatibility with released CLIs is unverified. A test runs the documented snippet and ingests the fixture into a ledger, and fails if either output stops matching the page.
