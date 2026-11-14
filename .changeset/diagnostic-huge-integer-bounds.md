---
"agent-harness-lab": patch
---

Refuse an integer too large for a float with the usual `ValueError` instead of an `OverflowError` where diagnostics validate a number: a cascade cost, latency or cost limit, a drift reference window, window length or run start time, and an audit route rate or a sealed receipt's expected draws. `math.isfinite` raises on such an integer, so the check never reached its own refusal. Each case has a boundary test, a huge negative integer included. Part of #14.
