---
"agent-harness-lab": minor
---

Add `sources.knowledge_links`, an offline reader of the links between the Markdown pages of one selected knowledge bundle (OKF export, LLM wiki, Markdown knowledge graph or ADR directory). It turns `[[wikilinks]]`, embeds, inline links and images, reference definitions and autolinks into provenance-preserving edges with an explicit `resolved`, `missing`, `ambiguous`, `outside_bundle`, `remote`, `unknown` or `invalid` status, skips code, never opens files or fetches remote links, and refuses oversized bundles instead of truncating them. Its counts-only `knowledge-links-v1` summary is never publishable.
