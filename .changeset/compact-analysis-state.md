---
"agent-harness-lab": patch
---

Analysis now holds less state per event. The recurring-sequence suffix automaton keeps its lengths, links and positions in parallel arrays, stores a single outgoing transition as a pair instead of a dict, releases transitions before counting roots, and keeps only each state's root count rather than a set for every state. Event references stay `(case_id, event_id)` pairs until they are emitted as evidence, observations replayed for method selection are value tuples instead of dicts, and cases whose capture order and timestamps have both become unusable drop their stored events because they can no longer be selected. Observation cache entries are tuples too. On a synthetic 192k-event corpus, peak traced memory fell from 2.3 KB to 0.6 KB per event. `analyze()`, `report()` and `ahl analyze` outputs, ordering, counts, findings and receipts are byte-identical.
