---
"agent-harness-lab": patch
---

Config recommendations now refuse with `ValueError` a forged inventory whose rows are each valid but that no capture writes together, such as two same-name server rows without their scope precedence or a settings digest list replaced by a scalar, where they previously crashed with `KeyError` or `TypeError`. A user-layer output style, whose precedence over a same-name project style is not recorded, is now `unverified` and carries `shadowed: null`. `not_inventoried` now lists managed MCP servers, managed settings policies and Codex managed configuration. Plugin bundle `status` returns `dirty_checkout` when a touched path became a symlink, directory, hardlink or special file, and refuses a malformed manifest with `ValueError`. A checkout whose worktree walk crosses the folder entry, depth or byte bounds gets the `checkout_bound_exceeded` receipt instead of an error.
