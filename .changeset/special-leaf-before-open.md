---
"agent-harness-lab": patch
---

Refuse special files before opening the leaf. `open_nofollow` now runs `lstat` on the final name relative to its walked parent and refuses anything but a regular file before the open, so an allowlisted device node is never opened and a Unix socket raises the labelled `ValueError` (`artifact must be a regular single-link file` for artifact extraction, `input must be a bounded single-link regular file` for other reads) instead of a bare `OSError`. The descriptor is still checked with `fstat` after the open. FIFO test cases now run on a thread with a timeout, so a blocking open fails the test instead of hanging the suite.
