---
"agent-harness-lab": patch
---

The complete control of every authored task is also graded in a workspace that holds the harness files a trial gets, so a grader that counts new or changed files in the tree is sent back to its author instead of failing every trial of both arms. The author is told to ignore those files. Two failure reasons count as the same when they differ only in the names, paths and numbers each arm put into them. A refining proposer is told never to write a rule whose only support is a failed check, nor one that contradicts another rule of the same file.
