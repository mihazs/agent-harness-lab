---
"agent-harness-lab": minor
---

Classify each tool error result of a pinned native Claude capture against a fixed, versioned catalog of runtime-generated messages (`ahl-tool-error-catalog-v1`: read before edit, edit target not unique or not found, file not found or changed, permission denied, user rejected or interrupted, command timeout or non-zero exit, unknown tool, input validation) and record only the class as `tool_error_class`; every other body, every look-alike text and every release outside the catalog's pinned runtime range is `unclassified`, and no message text or excerpt is stored. Mechanism summaries and the brief report `errors_by_error_class` and `cases_by_error_class` per tool group plus a `tool_error_classes` total, so a repair hypothesis such as a read-before-edit reminder can be tested and a probe can target it; errors from a source that records no class stay `not_captured`.
