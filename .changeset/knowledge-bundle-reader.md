---
"agent-harness-lab": minor
---

Add `sources.knowledge_bundle`, an offline reader for canonical OKF v0.2 bundles, Git-backed wikis, repository documentation and local wiki exports under an explicit allowlisting manifest. It reads an extracted directory or one supplied ZIP in memory with refusal of symlinks, hardlinks, traversal, duplicate members and compression bombs, reports malformed frontmatter and unknown OKF versions as findings, keeps `executor`/`attester` fields inert, and exposes claimed trust and lifecycle tiers without verifying them. `knowledge_links.links` gains `root_relative=True` to resolve OKF `/`-prefixed links against the bundle root. Part of #47.
