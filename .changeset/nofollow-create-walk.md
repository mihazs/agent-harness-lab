---
"agent-harness-lab": patch
---

Create, lock and open root directories through the no-follow component walk, so a parent directory swapped for a symlink after the caller's checks is refused instead of followed. The new `open_beneath(path, flags, mode=0o600)` reuses the walk and opens the final name relative to its parent with the caller's flags, always adding `O_NOFOLLOW | O_CLOEXEC`. Private journal writes and the journal lock use it; project configuration creation, working-state capture, analysis snapshots and publication, runtime image scans and the delegated cgroup root open their directories with `open_directory_nofollow`. Exclusive-create and lock behaviour, permissions and error messages are unchanged; a swapped ancestor raises `input crosses a symlink`.
