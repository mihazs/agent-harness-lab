---
"agent-harness-lab": minor
---

`ahl improve` now takes `--memory-store`, `--incremental` and `--explain-reuse` with an explicit `--events` capture and reuses the verified process-mining output of an unchanged capture and harness, the same opt-in stage `ahl analyze --incremental` reuses. The receipt gains the `reuse` object, and the `evidence_collection` row of the `incremental` block becomes `reused` (`hit_verified`) only for a verified hit and otherwise `rerun` with the typed miss or invalid reason; an applied, partially applied or hand-edited harness and a changed capture are `miss_input_changed`. Preparation, evaluation construction and trials are never reused, and `--incremental` is refused with `--history`. Receipt parity between the CLI and skill routes ignores `reuse`, which follows the store like `incremental` follows the run order.
