# What analysis runs

`ahl analyze .` inspects local evidence without a model call; `ahl analysis .`
is its alias. `ahl improve .` collects the same evidence before candidate preparation
and evaluation. Neither requires a separate mining step. See [workflows](workflows.md)
for the operating steps.

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
order; a valid capture ordinal does not establish elapsed time or causality. A
versioned within-line subordinal can break capture-ordinal ties as declared capture
order; see [declared source order](#declared-source-order).

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

The [method-selection rationale](analysis-method-rationale.md) describes the
research context and limits of this policy.

Private `analysis.json` contains the graph, findings, execution receipt and
collection metadata. `report.md` presents counts, method statuses and caveats.
Discovery, collection, analysis and completion progress is printed on stderr,
using only aggregate counts and elapsed time. Improvement also reports candidate
preparation and evaluation stages. Progress does not print capture paths, session
identities or bodies. These artifacts remain private even if they contain only
metadata; the tool's publication policy still applies.

### Declared source order

The pinned Claude profile can emit several normalized facets at one physical
record ordinal: content blocks of one line and the response usage/stop facets it
supports. Profile `claude-native-order-v1` records a separate subordinal for them
in `source_order_profile`, `source_order_segment` and `source_suborder`:

| Field | What it records | What it does not establish |
| --- | --- | --- |
| `capture_order` | Zero-based line position, offset by earlier files in a project-root journal (unchanged) | Execution time, concurrency or causation |
| `source_suborder` | Content-array position within that line; usage and stop facets follow the line's blocks, in that order | That blocks ran in array order; facet placement is a profile declaration, not an observed write |
| `source_order_segment` | The physical file within one projection: `0` for a single file, one per file in a project-root journal | Any order between files |

When capture ordinals tie, method selection may treat the strictly increasing
key `(capture_order, source_suborder)` within one segment and profile as
*declared capture order*. Cases ordered only this way are reported in
`method_selection.inputs.source_order` (`declared_order_only_cases`, and per-method
`declared_capture_order_cases` in the receipt coverage). Remaining limitations stay
explicit: a tie whose records lack a usable subordinal, a repeated or reversed key,
and an unrecognized profile leave the case unordered (`unresolved_tied_cases`).
When one case's records come from different segments, its capture order is
unknown even if offset ordinals increase (`cross_segment_cases`); the order of
copies or archives of one session is not established. Valid strictly increasing
timestamps remain an independent route, so such a case can still be ordered by
its timestamps. The receipt reports both parts: `cross_segment_unordered_cases`
stay unordered and `cross_segment_timestamp_ordered_cases` are ordered only by
timestamps. Hashed block identities and response IDs never supply order, and no
timestamp is synthesized.

The subordinal changes only which cases qualify as ordered inputs. It adds no
outcome, success/failure or independence label, and recurrence and matching
outputs keep their descriptive authority. One file holds one writer and therefore
one reported root, so recurrence over pinned Claude captures still needs three
roots, for example a normalized file combining exported ledgers of separate
sessions. Claude records carry no input/result/state signatures, so state-aware
matching stays unselected for them.

For the pinned Claude adapter the new fields stay outside event IDs, journal
record identities and revisions, the collection fingerprint and duplicate-UUID
comparison; they derive from `capture_order`, the hashed block index and the file
being read. Inputs without these fields keep their previous receipts byte for
byte, and the `source_order` summary appears only when a record declares one.
A profile is accepted only on records of its producer (`source_format: claude`,
`source_version: claude-jsonl-2.1.316`); partial declarations and unknown profiles
are rejected. A normalized ledger, such as an exported `events.v2.jsonl`, may
carry the fields as caller declarations, like `capture_order`. The summary then
reports `declared_by: caller` instead of `native_adapter`, and the collection
fingerprint includes the fields because they cannot be rederived from the
ledger. Broader cross-file ordering remains open in
[#190](https://github.com/mihazs/agent-harness-lab/issues/190).

## Public benchmark report input

`ahl benchmark-report --public` accepts a nonempty JSON list of flat matched-pair
records. Each record requires `baseline_runtime`, `candidate_runtime`, and
`provenance`; the only additional record fields are `status`, `execution_kind`,
and `publication_allowed`. If supplied, `publication_allowed` must be `true`.
Unknown fields, profiles, and metadata wrappers are rejected before any report
directory is created.

Each runtime object supplies `provider`, `runtime`, `model_revision`, `reasoning`,
`task_bank_revision`, `sandbox_image`, `permissions`, and `controller_version`.
The values must match within a pair. The only additional runtime field is
`effective_settings_verified`; it must be `true` in both objects for the settings
to be reported as verified, and must be a boolean when supplied. The eight
runtime values are nonempty strings of at most 4096 characters.

`status` may be `unknown`, `not_run`, `not_executed`, `completed`, `passed`,
`failed`, `skipped`, `inconclusive`, `blocked`, `incomplete`, `interrupted`,
`budget_exhausted`, `execution_error`, `invalid_input`, `grader_error`, `timeout`,
`agent_timeout`, `rejected`, or `supported`. `execution_kind` may be `synthetic`,
`replay`, `real_model`, or `fake_agent`. When omitted, these default to `unknown`
and `synthetic` respectively.

The provenance object contains only `source_kind`, `approved_publication`,
`source_ref`, and `derived_from_private`. Public export requires `source_kind` to
be `synthetic` or `public`, `approved_publication` to be `true`, a nonempty
`source_ref` string of at most 4096 characters, and `derived_from_private` to be
`false`. These are caller assertions;
the command does not authenticate their origin or prove that data is public.
Without `--public`, the existing private report input path is unchanged.

## Optional algorithms and remaining integration

| Capability | Current entrypoint / prerequisite | Default analysis? |
| --- | --- | --- |
| Lexical incident retrieval | Explicit `--incident-corpus` and `--incident-query`, or standalone `ahl incident-search` | Only when both inputs are supplied |
| Classifier comparison | `ahl benchmark`, supplied labels and selected local models | No |
| Paired baseline/candidate execution | Applicable `ahl improve` workflow or explicit evaluation/experiment | No model execution in read-only analysis |
| Bounded candidate search | Guarded search APIs with explicit contracts | Not part of read-only analysis |
| Automatic behavioral application | Independent certification and runtime prerequisites | Not available through the ordinary cooperative workflow |

### Explicit incident retrieval

`ahl analyze . --incident-corpus /private/incidents.json --incident-query "tool timeout"`
adds the existing local lexical method to the capture portfolio. The same options
apply to `ahl improve` and its evidence stage. No query is inferred from captures,
no corpus is discovered, and retrieval makes no model/provider calls. Ordinary
improvement execution retains its existing agent and evaluation requirements.
Watch does not accept these options; its existing capture-change checkpoint remains
unchanged. The receipt's capture evidence state still describes captured history,
while the separate retrieval method reports completed corpus work even with no captures.

The corpus is a JSON list of Incident records, each containing `incident_id`,
`root_id`, `summary`, string lists `labels` and nonempty `evidence_refs`, `reviewer`,
`provenance: "reviewed_annotation"`, `sampling: "random"` or `"signal_enriched"`,
`source_kind: "synthetic"`, `"public"` or `"private"`, and boolean `approved: true`.
These validated fields are caller declarations, not authenticated review or
independence. Supplying a file does not establish approval. Missing, partial or
invalid input fails explicitly; a valid empty corpus or zero matches completes
retrieval and grants no correctness authority.

Private `analysis.json` contains ranked summaries and evidence references; the
execution receipt reports the corpus revision, query digest, record/match counts
and actual completed method. Controlled improvement preparation receives the same
untrusted results. Summaries are evidence, never instructions. Retrieval stays
uncalibrated; root overlap with capture evidence and independence remain unknown,
and matches never establish a mechanism, a vote or adoption authority. Contextual
results and their marked facets remain blocked by the supported publication guards,
including when synthetic or public corpus records were supplied. Generic standalone
lexical retrieval retains its existing contract.

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
