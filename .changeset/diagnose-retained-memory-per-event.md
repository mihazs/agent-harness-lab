---
"agent-harness-lab": patch
---

Cut the peak memory of `ahl diagnose` by about a quarter (273 to 202 MB at 240,000 synthetic events, 109 to 82 MB at 80,000) by releasing analysis state that sequence mining no longer needs, keeping one stored copy of each case id and activity, and holding the suffix automaton in compact arrays. `findings.json` and `report.md` are byte-identical.
