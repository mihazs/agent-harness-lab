---
"agent-harness-lab": patch
---

Raise the installer's release-archive member bound from 1000 to 4096. The shipped tree had grown to 1004 entries, so `install.py` refused the release it was built with (`invalid release archive size`) and every packaging test that installs the archive errored. A regression test now installs the archive built from the checkout and requires it to stay within three quarters of the bound, so growth is caught before the installer refuses a release.
