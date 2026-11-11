---
"agent-harness-lab": minor
---

`ahl benchmark` now discloses projects shared by train and heldout rows and normalized texts that appear in several splits, with `--reject-leakage` to fail on either. Per-split metrics add `positives` and `positive_misses` so high accuracy cannot hide missed rare positives. Exact-copy detection does not find paraphrases or establish independence. Part of #14.
