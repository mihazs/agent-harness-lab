---
"agent-harness-lab": patch
---

The evaluation runner now splits agent JSON Lines traces on line feeds only, tolerating CRLF endings. A record whose string contains a raw U+2028, U+2029 or U+0085 parses as one record instead of being reported as `invalid_trace`, and U+000B, U+000C and U+001C to U+001E are no longer treated as record boundaries. Traces without those characters are classified exactly as before.
