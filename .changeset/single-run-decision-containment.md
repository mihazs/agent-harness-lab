---
"agent-harness-lab": patch
---

Fix the default `ahl improve` single-run `decision` block so it can never change a run's outcome. It no longer hashes the whole evaluation tree. That hash raised on routine trial-workspace symlinks (virtualenvs, `node_modules/.bin`) and turned `no_change` or `evaluation_failed` runs into `invalid_input`. The block now binds bounded `results.json` and manifest hashes, recording `unknown` when they cannot be read. A failure to build the explanation yields `{"status": "unavailable"}` and leaves the result unchanged. The block also reports `difference_outside_gate_splits` when only held-out or undeclared splits differ. It takes splits from the manifest, so a trial reporting another split counts as unknown with a `trial_split_mismatch` reason. It records `final_attempt` and `describes_final_attempt`.
