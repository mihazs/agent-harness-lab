---
"agent-harness-lab": patch
---

Claude trials no longer see the sandbox's root placeholder entries as untracked files, no longer fail `git checkout -b <branch> origin/main` on the read-only `.git/config`, and can no longer read the host's `/tmp` from commands.
