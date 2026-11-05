---
"agent-harness-lab": patch
---

Route the remaining full-path no-follow opens through the component walk, so a parent directory swapped for a symlink after containment is refused instead of followed. Bank revision tree fsyncs, regression case evidence fsyncs and lineage snapshot fsyncs now open files with `open_nofollow`, and `sync_directory` opens directories with the new `open_directory_nofollow`, which reuses the same walk and opens only the final component `O_RDONLY | O_DIRECTORY` so it can be fsynced. Error messages and call-site signatures are unchanged; a swapped ancestor raises `input crosses a symlink`.
