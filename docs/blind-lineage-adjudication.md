# Check task lineage with blind adjudication

[Bank declarations](bank-qualification.md) · [Content grouping](content-grouping.md)

A `reviewed` independence label is written by the bank author, so it says what the
author believes. Blind adjudication adds a separate check: two or more reviewers who
see only opaque aliases, prompts and fixture hashes say which tasks look related.
AHL compares their verdicts with the roots and partitions the bank declares. It
executes nothing, calls no model and needs no network.

Use it on a private bank before relying on a `reviewed` label. The result is
descriptive: it can find relations the bank missed, but it cannot prove that tasks
are independent.

## Write the packet

```sh
ahl bank --manifest PRIVATE_BANK.json --adjudication-packet PRIVATE_DIR/packet.json
```

The output path must be new, outside the bank directory and free of `..` and
symlinks. The file is created owner-only. The JSON result carries the packet's
`packet_sha256`, which every verdict must repeat.

Each packet item has only `alias`, `prompt`, `fixture_sha256` and `fixture_files`
(file name to hash). Task IDs, roots, families, partitions, project and time groups,
source, reviewer, review status and the independence label are left out, and so are
grader bundles. Aliases are derived from the exact bank bytes, so a changed bank
produces a different packet. The packet contains your prompts: keep it private and
give it only to the adjudicators.

## Collect verdicts

Each adjudicator reads the packet alone and writes one JSON file:

```json
{
  "schema_version": 1,
  "profile": "blind-lineage-adjudication-v1",
  "packet_sha256": "PACKET_SHA256",
  "adjudicator": "judge-a",
  "related_groups": [["ALIAS_1", "ALIAS_2"]],
  "unsure": ["ALIAS_3"]
}
```

`related_groups` lists sets of two or more aliases the adjudicator believes share a
root, template or origin. `unsure` lists aliases they could not judge. An alias may
appear once in total, and every alias must exist in the packet. Use empty lists when
nothing is related and nothing is unclear. Extra fields, a different packet hash, an
unknown alias or a verdict file over 1 MiB are refused.

The `adjudicator` name must be unique across the files and must not equal any
reviewer named in the bank (compared without case). Give the adjudicators no access
to the bank, to each other's files or to the task author's notes. AHL cannot check
that.

## Evaluate

```sh
ahl bank --manifest PRIVATE_BANK.json --adjudications judge-a.json judge-b.json
```

Provide one to sixteen files; fewer than two cannot support a label. The report is under
`blind_adjudication`. The command writes nothing and exits 3 like every other bank
inspection, because certification stays ineligible.

### Outcome

| `outcome` | Meaning |
| --- | --- |
| `insufficient_adjudicators` | Fewer than two verdicts. Nothing supports a `reviewed` label. |
| `relations_found` | Every adjudicator related the same two tasks and the bank does not group them under one root, in one partition or across two. Fix the bank before anything else. |
| `inconclusive` | Adjudicators disagreed, or someone marked tasks unsure. |
| `no_relation_found` | No adjudicator found an undeclared relation. This is not verified independence. |

### Per-task status in `tasks`

From most to least severe:

1. `cross_partition_relation`: all adjudicators related it to a task in another partition.
2. `undeclared_relation`: all adjudicators related it to a task in the same partition that has a different declared root.
3. `disputed`: some, but not all, adjudicators related it to another task.
4. `unsure`: an adjudicator could not judge it. Pairs that involve an unsure task are ignored.
5. `adjudicated_independent`: no adjudicator related it to a task outside its declared root.

A pair inside one declared root and partition is the declared grouping and is not
reported. `declared_reviewed_without_adjudicated_independence` lists every task whose
label says `reviewed` but that lacks a quorum or a status of `adjudicated_independent`;
treat these labels as unsupported.

## What this does not establish

The report always carries `independence_verified: false`,
`certification_eligible: false`, `authenticated_adjudicators: false` and
`publication_allowed: false`, plus these limitations:

- Adjudicator identities are operator-declared, not authenticated.
- Collusion and shared model or training lineage between adjudicators are not excluded.
- Blindness covers declared labels and other verdicts, not provenance that a prompt or file name reveals.
- No relation found by the declared adjudicators is not verified independence.

It also does not replace the content checks in [content grouping](content-grouping.md),
the held-out controls in [bank qualification](bank-qualification.md) or the signed
review in [conditional final-bank review](conditional-bank-review.md). Keep the
packet, verdicts and report in private state. Public examples must use the bundled
synthetic banks.
