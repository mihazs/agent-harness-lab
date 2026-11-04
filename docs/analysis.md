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

## Evidence-based method selection

`method_selection` records the versioned structural prerequisite policy after
streaming coverage discovery. Selection uses captured fields and ordering support,
never whether a method produced a favorable result. Every applicable complementary
method remains selected even when it finds nothing. Optional corpus, model and
execution methods retain the explicit prerequisites listed below.

The graph describes input capture-stream adjacency, which remains useful when
execution order is unknown. Recurrence selects multi-event cases with strictly increasing
declared `capture_order` or strict valid timestamps, requiring three reported roots
in that subset. State-aware matching selects ordered cases with complete
tool/input/result/state signatures and then checks call/effect eligibility.
Unordered cases do not suppress supported cases; per-method excluded coverage is
reported explicitly. Structured checks require explicit execution status or a
referenced contract flag. Tied, absent or reversed timestamps cannot establish
order; a valid capture ordinal does not establish elapsed time or causality.

The pinned Claude profile can emit several normalized facets at one physical
record ordinal. Its current normalized contract does not retain a separate total
order for those facets; hashed block identities and parallel content-array
positions cannot supply execution chronology. Such cases remain explicitly
excluded from recurrence/stateful matching while the descriptive graph retains
them. The source-ordering follow-up is tracked in
[#190](https://github.com/mihazs/agent-harness-lab/issues/190).

The all-case descriptive graph remains available when prerequisites fail. The
receipt records actual method dispatch as `completed` or `not_run` and distinguishes
insufficient support from completed work with no findings. The explicit low-level
`ahl diagnose` raw-analysis interface retains its legacy behavior; default
`ahl analyze` and `ahl improve` use the selected portfolio.
`synthesis` identifies contributing methods; method receipts retain excluded coverage. All views
share a captured-event dependency group: agreement creates no independent vote,
calibrated confidence or promotion authority. Findings with different questions
are not manufactured into a contradiction; case-level observations and unresolved
questions remain available for review. Absence of supported patterns is not proof
that the harness is correct.

Each receipt distinguishes `discovery_input_events` from method-specific
`input_events`; a skipped method has zero dispatched inputs. The method plan and
synthesis carry the same private profile as the execution receipt even when
extracted for supported local reporting.

The policy adapts algorithm-selection principles from
[Rice (1976)](https://doi.org/10.1016/S0065-2458(08)60520-3), event-quality guidance
from the [Process Mining Manifesto](https://www.vdaalst.com/publications/p658.pdf),
and the distinction between complementary methods and independent bias structures
in [Lawlor et al.](https://pmc.ncbi.nlm.nih.gov/articles/PMC5841843/).
[Cawley and Talbot](https://jmlr.org/papers/v11/cawley10a.html) explain why selecting
on finite observed performance can bias evaluation. These motivate a transparent
prerequisite policy; they do not establish that AHL's policy is unbiased or improves
diagnostic accuracy. That requires a separate frozen, independently labeled study.

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
