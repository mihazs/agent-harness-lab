# What analysis runs

Run `ahl analyze .` to inspect local evidence without a model call. `ahl analysis .`
is an alias. Run `ahl improve .` to collect the same evidence and then prepare and
evaluate supported candidates. Neither command requires a separate mining step.

## Default methods

| Method | Implementation | What it establishes |
| --- | --- | --- |
| Directly-follows graph | Streaming adjacency counts per case, with reported-root support | Which recorded activities follow each other in capture order |
| Run-collapsed projection | Same graph after collapsing consecutive equal activities | A descriptive view of transitions; the raw graph remains available |
| Recurring sequences | Suffix automaton over per-case activity sequences; minimum three reported roots | Shared contiguous routines; root independence remains unverified |
| Duplicate observations | State-aware matching of tool, input, result and state revision | Matching observations at a recorded state; necessity remains unknown |
| Structured checks | Explicit execution-error fields and caller-supplied contract flags | Observed error/contract signals; expected negative tests and recovery may explain them |

The shared graph implementation is in
[`process_mining.py`](../agent_harness_lab/diagnostics/process_mining.py).
Sequence and observation analysis are composed in
[`tools/diagnose.py`](../tools/diagnose.py); collection and structured tool errors
are handled by [`evidence.py`](../agent_harness_lab/evidence.py).
Both cooperative and controlled preparation receive descriptive graph metadata.

These are descriptive process-mining methods. They do not perform Petri-net
discovery, conformance alignment, causal inference, or automatic failure attribution.
Counts cannot certify task completion, active time, waste or a beneficial edit.
Malformed timestamp strings are counted as unusable timestamp metadata in default
analysis, while capture order is preserved. The low-level `mine` command retains
its stricter input validation. Its legacy `independent_roots` field is a compatibility
alias for `reported_roots`, not a verification claim.

## Read the result

Stdout is one JSON workflow receipt. Its `analysis` object includes:

- `evidence_state`: `empty`, `incomplete` or `observed`.
- `algorithms`: executed method IDs, descriptions, status and output counts.
- `not_run`: optional analysis families and their missing inputs or separate entrypoints.
- `caveats` and `next_action`: coverage limitations and the next useful step.

`status: analyzed` means the read-only operation completed. It does not mean the
harness is correct. A method can report `no_events` or `insufficient_support`
rather than a negative diagnosis. For example, fewer than three reported roots
cannot establish a recurring sequence under the implemented threshold.

Private `analysis.json` contains the graph, findings, execution receipt and
collection metadata. `report.md` presents counts, method statuses and caveats.
Discovery, collection, analysis and completion progress is printed on stderr,
using only aggregate counts and elapsed time. Improvement also reports candidate
preparation and evaluation stages. Progress does not print capture paths, session
identities or bodies. These artifacts remain private even if they contain only
metadata; the tool's publication policy still applies.

## Optional algorithms and remaining integration

| Capability | Current entrypoint / prerequisite | Default analysis? |
| --- | --- | --- |
| Lexical incident retrieval | `ahl incident-search`, explicitly approved reviewed corpus | No |
| Classifier comparison | `ahl benchmark`, supplied labels and selected local models | No |
| Paired baseline/candidate execution | Applicable `ahl improve` workflow or explicit evaluation/experiment | No model execution in read-only analysis |
| Bounded candidate search | Guarded search APIs with explicit contracts | Not part of read-only analysis |
| Automatic behavioral application | Independent certification and runtime prerequisites | Not available through the ordinary cooperative workflow |

The audit identified missing default graph integration and unclear method receipts,
tracked in [#174](https://github.com/mihazs/agent-harness-lab/issues/174) and
[#175](https://github.com/mihazs/agent-harness-lab/issues/175). Progress is owned by
[#171](https://github.com/mihazs/agent-harness-lab/issues/171). The broader remaining
work already has owners: configuration/preflight [#142](https://github.com/mihazs/agent-harness-lab/issues/142),
CLI/skill parity [#143](https://github.com/mihazs/agent-harness-lab/issues/143),
mechanism routing [#10](https://github.com/mihazs/agent-harness-lab/issues/10),
search integration [#12](https://github.com/mihazs/agent-harness-lab/issues/12) and
verified behavioral application [#77](https://github.com/mihazs/agent-harness-lab/issues/77).
The descriptive analysis changes do not close those broader issues.
