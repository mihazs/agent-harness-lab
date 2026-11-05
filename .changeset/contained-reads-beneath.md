---
"agent-harness-lab": patch
---

Open contained reads one path component at a time so a parent directory swapped for a symlink after the containment check is refused instead of followed. `read_regular`, `regular_sha256`, `regular_size`, `regular_equal`, `tree` and `artifacts` now walk from `/`, opening each directory by name from its already opened parent with `O_DIRECTORY | O_NOFOLLOW` (`O_PATH` on Linux, so search-only directories still resolve) and the leaf with `O_NOFOLLOW | O_NONBLOCK`, then keep the regular-file, single-link and byte-bound checks on the final descriptor and the 1 MiB streaming chunks. Every intermediate descriptor is closed on all paths. Platforms whose `os.open` lacks `dir_fd` support refuse these reads. Call-site signatures are unchanged.
