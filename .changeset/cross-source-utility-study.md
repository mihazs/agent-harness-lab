---
"agent-harness-lab": minor
---

Add `agent_harness_lab.sources.utility_study`: a preregistered offline study that scores a session-only baseline and a cross-source arm on one frozen case set (join precision and false-finding rate, contradiction identification, actionable-finding yield, source coverage and records-read overhead). Edited, dropped or extra cases are rejected against the recorded hash; the preregistered rule returns `supported_on_fixture`, `not_supported` or `inconclusive` and never claims agent success, production precision or independent roots. Part of #58.
