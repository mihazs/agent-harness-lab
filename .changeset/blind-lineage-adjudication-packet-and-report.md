---
"agent-harness-lab": minor
---

Add blind lineage adjudication for task banks (part of #8). `ahl bank --manifest BANK --adjudication-packet PATH` writes a new owner-only packet of opaque aliases, prompts and fixture hashes that omits task IDs, roots, families, partitions, reviewers and labels. `ahl bank --manifest BANK --adjudications FILE...` compares two or more independent verdict files with the declared roots and partitions and reports cross-partition relations, undeclared relations, disputes and unsure tasks, and lists every `reviewed` task that no adjudicator quorum supports. Adjudicators must be distinct and must not be a reviewer named in the bank; a changed bank invalidates old verdicts. Adjudicator identities are operator-declared, so the report stays descriptive: `independence_verified` and `certification_eligible` remain false, and finding no relation is not verified independence. Existing `ahl bank` behavior is unchanged without the new flags.
