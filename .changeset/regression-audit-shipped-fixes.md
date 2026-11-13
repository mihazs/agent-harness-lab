---
"agent-harness-lab": patch
---

Audit the fixes shipped since 0.153.0 against their regression tests and close the gaps found: direct boundary tests for the controller-owned digest drop in author normalization (refused by default, dropped only on request), bytecode caches in the controlled-construction role views, the unknown-coverage marker of a truncated event store (every written case, byte and time bounds, rows without a case ID, full detail), the no-change disposition for trials that are not verified successes, a killed publish retried with other dependencies in the memory store, and the benchmark driver (a differing report hash, a failed or invalid run never reported as a measurement, the driver's own peak memory, digests streamed instead of read whole). Seven existing guard tests gain a `# fixed-in:` pin. No runtime behavior changes. Part of #781.
