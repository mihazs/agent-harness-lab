---
"agent-harness-lab": patch
---

Count Claude-Mem search hits against the row bound. `max_rows` now bounds the records an export admits rather than its rows: each session row counts once, each observation, summary and prompt row twice (its claim and its `search_hit`), plus the one `claude_mem_export` record. The documented 100,000 ceiling previously admitted up to 200,001 records; it now admits at most 100,000. Rows later rejected for missing identity still count, because the bound is checked before rows are mapped.
