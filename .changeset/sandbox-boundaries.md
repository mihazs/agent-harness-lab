---
"agent-harness-lab": patch
---

Reject writable sandbox workspaces that overlap read-only inputs. Bound combined
stdout/stderr capture, clean up workers when selector setup fails, and count
preflight work against the execution deadline. Share one wall-clock budget across
offline grader audits and preserve completed checks on timeout or exhaustion.
