---
"agent-harness-lab": patch
---

Measure the traced-memory bounds of the JUnit and pytest JSON report tests in a fresh single-thread interpreter, so another thread's allocations (three default I/O buffers of a spawned child, 393,216 B, were the excess of two CI failures) and first-use caches no longer decide them; no bound changed, and each now has a deliberate-regression check. No runtime behavior changes. Closes #763.
