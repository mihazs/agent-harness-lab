---
"agent-harness-lab": patch
---

Bound scalar values in hostile JSON exports before decoding. The shared screen for pytest JSON reports and Claude-Mem exports now also counts strings, numbers and literals outside object keys, in the same streaming pass that skips string contents, against a new `max_json_values` ceiling that a selection may only lower: 2,500,000 for pytest JSON and 4,000,000 for Claude-Mem. Admitted scalar-heavy documents peak at about 11.3 times their size for short strings and 9.2 times for floats, so the ceilings cap the decoded tree near 135 MiB and 216 MiB. An unterminated string is now refused in one pass instead of being rescanned from every later quote. The loader, `limits` and `HostileReport` move to `agent_harness_lab/sources/hardened.py` and stay importable from `sources.test_results`, and both sources share one read, hash, re-hash and selection check.
