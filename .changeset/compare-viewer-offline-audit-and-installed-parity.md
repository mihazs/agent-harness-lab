---
"agent-harness-lab": patch
---

Guard the `ahl compare` static viewer with a browser-free audit of the rendered page (hostile text in every displayed field stays text, allowlisted tags and attributes, in-page links only, keyboard structure, with a mutation that stops escaping failing it) and with installed-launcher and source/wheel/archive layout checks that render the golden report without remote assets. No runtime behavior changes. Part of #37.
