---
"agent-harness-lab": patch
---

An installing `ahl improve` run may repair two defects of the authored evaluation manifest in turn (it was one), and the construction prompt now asks for exactly one broken, one partial and one complete control per task, so a model that writes a second defect after the first repair no longer ends the run `invalid_input`. The grader self-test now grades a throwaway copy of each control fixture, so a grader that inspects a git repository (which writes into it) no longer ends the run with `grader_input_changed_after_freeze`; a grader that writes into its own frozen program or bundle still does. Hardlinked files in the authored evaluation (what `git clone` of a local repository creates) are given their own inode after construction instead of ending the run `run_input_rejected`; the construction prompt now asks for `git clone --no-hardlinks`.
