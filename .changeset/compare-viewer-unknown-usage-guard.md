---
"agent-harness-lab": patch
---

The static comparison viewer tests now assert that an unknown usage total renders as `unknown` with a non-zero unknown-record count, so a regression that shows it as zero fails. Test-only change. Follow-up to #37.
