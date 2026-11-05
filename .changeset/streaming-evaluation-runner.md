---
"agent-harness-lab": patch
---

The evaluation runner now uses less peak memory. The input `digest` hashes each file through one reused 1 MiB buffer, so it no longer holds two copies of every file. `trace_result` classifies the agent stream in one pass and keeps only the flags, counts and usage it needs, not every parsed event. `results.json` is written with `json.dump` to a temporary sibling that replaces the file once complete, so the runner no longer builds the whole document as a string after every trial, and a serialization error still leaves the previous `results.json` in place. Digests, trace outcomes, `results.json` bytes, file modes and errors are unchanged.
