---
"agent-harness-lab": patch
---

Make the `ahl skills` receipt say what happened. A first install that replaced nothing now prints `Linked skills.` and leaves no empty backup folder, instead of naming a new empty folder as "Previous directories". A foreign symlink or ordinary directory under the retired `harness-evaluation` name was left alone without a word; every run now names it (`Left untouched, not installed by this tool: PATH`), so a user can see that an old skill of that name still shadows the migration.
