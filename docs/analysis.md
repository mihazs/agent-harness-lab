# What analysis runs

`ahl analyze .` inspects local evidence without a model call; `ahl analysis .`
is its alias. `ahl improve .` collects the same evidence before candidate preparation
and evaluation. Neither requires a separate mining step. See [workflows](workflows.md)
for the operating steps.

Evidence and ledger staging use the selected output directory instead of the
ambient temporary directory. Output paths must remain stable and trusted during
the run: path-based operations do not prevent a concurrent directory replacement.
Saved-history analysis (below) is the only route that reads another input store;
it adds a destination check but no no-write guarantee against a concurrent writer.

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

## Analysis brief and mechanism summaries

Each analysis run writes two additional private artifacts beside `analysis.json`.
Both are descriptive observed patterns; neither is a diagnosis, an outcome label or
an independence claim.

| Artifact | Purpose | Bound |
| --- | --- | --- |
| `analysis-brief.json` | What the cooperative preparation stage reads instead of `analysis.json` | Hard cap of 32 KiB on the file, independent of history size |
| `mechanism-summaries.json` | Ranked groups with at most five evidence references each | At most 200 groups; omitted groups are counted in `truncated` |

Structured tool errors are grouped by tool class and a fixed error category. A tool
class is a documented built-in tool name or a stable opaque label; free-text error
bodies, prompts, paths and identifiers are never copied into the brief. The category
is `tool_reported_error` because captured events carry only the structured error
flag; the vocabulary (`exit_code`, `permission`, `not_found`, `timeout`,
`schema_validation`, `network`) is reserved for sources that supply such a field and
is never inferred from text. Each group reports case and root counts, the error rate
against that tool's calls (null when no calls were captured), occurrence over four
merged capture-stream bins (not wall-clock time), and the observed next action:
`same_call_retry`, `same_tool_changed_input`, `same_tool_next`, `different_tool`,
`user_intervention`, `interrupted` or `abandoned` (no later action was captured;
a truncated capture explains it as well as abandonment). Groups rank by reported
roots, cases, bins with occurrences and error count. Counts are lower bounds when
`counts_are_lower_bounds` is true.

The brief also lists capture coverage and exclusions, the methods that ran and the
exact prerequisite each missing method lacks, weak-outcome signals, and a
`missing_evidence` list whose entries carry the command that would supply it. Weak-outcome signals
(user turn after a tool error, consecutive user turns, interrupts, same-call
retries, edit-then-revert) are `observed_pattern` with competing explanations. A
signal whose input is not captured is `unknown`, and a zero count is
`not_observed_capture_unverified`; neither is a clean result. Restated instructions
need content-free structural fingerprints of user messages, which the event envelope
does not carry, so that signal stays `unknown`.

### Bound and truncation provenance

The brief shrinks its lists from the end until it fits and records
`bounds.truncated`. Mechanism groups are capped at 2000 per run (further groups merge
into an `other` class) and case and root sets at 5000 per group, which sets
`counts_are_lower_bounds`. Truncation is reported and never turns missing data into a
success or an independence label. The size of `analysis.json` and `events.v2.jsonl`
is not yet capped.

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
| Saved paired-history analysis | Explicit existing `--history-store` and `--history-run` | Only when both inputs are supplied; no new trials |
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

### Explicit saved comparison analysis

`ahl analyze . --history-store /private/history --history-run RUN_SHA256`
uses the existing controller-history analyzer for that exact run. Both options
are required together and also apply to the evidence stage of `ahl improve`.
No history is discovered, initialized, resumed or retried, and the read dispatches
no task trials. Ordinary improvement retains its separately authorized runtime
behavior.

Supported contract, which fails closed:

- Only schema-1 unbound fixed histories are read. Bank-bound, protected, derived,
  sequential and preservation/cohort histories fail explicitly before their
  transitive validators run, because those validators may create dependency locks
  or signature-verification scratch inside the store. Their standalone commands
  keep their own contracts.
- The store must already be initialized, owner-only and hold its existing lock
  file; a missing lock fails without being recreated.
- The resolved `--output`, the default run directory and the lock directory under
  the state root must not equal, contain or sit inside the store. Symlink aliases
  are resolved first. The check runs before any lock, directory or scratch exists,
  for both `analyze` and `improve`.
- The check is a path comparison at start. Output and state paths must stay stable
  and trusted; a concurrent replacement of a directory ancestor, or a concurrent
  writer to the store, is outside this contract and is not prevented.

The existing analyzer checks frozen input identities, oracle state and sealed
trial evidence before computing its frozen fixed paired-root analysis. Unsealed
runs stay incomplete, quarantined runs stay blocked, and malformed or changed
evidence fails explicitly. Project `results.json` files are never substituted for
authenticated controller history.

The method receipt, private JSON, Markdown and improvement role views carry the
same conditional result and disposition. A statistical `supported` result does not
verify root independence or oracle review, grant promotion, or make capture-method
agreement independent corroboration; overlap with the captures stays unknown. The
completed historical read is distinct from executing paired evaluation, which the
receipt still lists as not run. All results stay private even when the original
experiment used synthetic tasks.

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

## Exit reasons and next actions

The CLI writes one short summary to stderr and preserves the JSON receipt on stdout. Empty evidence exits 0: analysis ran, but no capture was found. The first suggested step, `ahl audit .`, is offline and uses no model; `ahl improve` can incur model costs. Unknown or unrecognized reasons remain inconclusive; inspect the private receipt before retrying.

When a proposer writes `no_change`, the controller chooses the reason. `no_change_harness_supported`
requires an executable-grader success among the verified outcomes, no failure outcome and no collection
caveat. Every other case is `no_change_evidence_insufficient`: the receipt then carries a non-empty, ranked
`next_steps` list (strongest observed patterns, each missing-evidence item, then `ahl audit .`), each with
the command to run. Neither reason claims the harness is correct beyond the examined behaviors.

| Reason | Remediation |
| --- | --- |
| `agent_runtime_unavailable` | No requested runtime executable was found; install it or pass its trusted path with --codex or --claude. Authentication is not checked by AHL. |
| `analysis_incomplete` | Review caveats in analysis.json; restore missing coverage or remove explicit limits before interpreting absence of findings. |
| `analysis_observed` | Review the captured evidence and assess a candidate with ahl improve when ready. |
| `candidate_identical_to_baseline` | Review the unchanged candidate and its supporting evidence. |
| `capability_preflight_unavailable` | Restore the required preflight capability before retrying. |
| `certification_prerequisites_unverified` | Verify the certification prerequisites before drawing a conclusion. |
| `codex_credentials_unavailable` | Authenticate the selected runtime before requesting model execution. |
| `configuration_changed_before_execution` | Review the changed configuration before retrying. |
| `configuration_requires_recommendation_only` | Review the recommendation under the configured application policy. |
| `configured_bank_cannot_combine_with_managed_experiment` | Run without --experiment-request/--evaluation-store, or use the starter bank. |
| `configured_bank_digest_mismatch` | The configured bank changed; review it and set bank.sha256 in ahl.json to its new digest. |
| `configured_bank_invalid_or_unavailable` | Select an accessible valid project bank or use the starter bank; review its configuration before retrying. |
| `configured_bank_requires_cooperative_runtime` | Pinned project banks run only on the codex or claude runtime; set runtime in ahl.json to {name: codex or claude, executable: <absolute path>}. |
| `configured_bank_requires_development_regression_and_heldout` | Add development, calibration and held-out (certification or transfer) partitions to the bank. |
| `configured_bank_shared_trial_budget_exhausted` | Raise limits.trials in ahl.json to at least 1 + 2 * (bank task count). |
| `configured_executable_differs_from_requested_executable` | Align the executable with the project configuration. |
| `configured_executable_unavailable` | Provide the configured executable at its trusted path. |
| `configured_isolated_runtime_unsupported` | Select a supported runtime profile. |
| `configured_managed_bank_requires_controlled_workflow` | Managed banks run only through the controlled workflow; use a non-managed bank. |
| `configured_profile_does_not_allow_cooperative_improvement` | ahl.json profile is not cooperative; review it and set profile to cooperative and runtime to {name: codex or claude, executable: <absolute path>} to allow model execution. |
| `configured_responses_requires_controlled_improvement` | Select the controlled Responses improvement workflow. |
| `configured_runtime_differs_from_requested_runtime` | The requested runtime or executable differs from ahl.json; drop the override or update runtime in ahl.json. |
| `configured_shared_trial_budget_infeasible` | Increase the shared trial limit for the requested comparison. |
| `evaluation_no_improvement` | Review the evaluation results before proposing another candidate. |
| `executor_containment_or_state_failure` | Inspect the executor containment and state failure before retrying. |
| `fixed_comparison_inference_budget_infeasible` | Increase the declared inference budget for the comparison. |
| `fresh_comparison_incomplete` | Review the incomplete comparison and its missing trial coverage. |
| `frozen_inputs_changed` | Review the changed frozen inputs before starting a fresh evaluation. |
| `grader_sandbox_unavailable` | Provide the required grader sandbox capabilities. |
| `hook_adoption_requires_explicit_authorization` | Review the hook change and its authorization requirements. |
| `inference_aborted_after_failure` | Review the recorded inference failure before retrying. |
| `inference_protocol_error_no_retry` | Inspect the recorded inference protocol error before any retry. |
| `inference_request_bytes_exhausted` | Review the configured inference request byte limit. |
| `inference_request_count_exhausted` | Review the configured inference request limit. |
| `inference_response_bytes_exhausted` | Review the configured inference response byte limit. |
| `inference_sandbox_unavailable` | Provide the required inference sandbox capabilities. |
| `inference_tool_count_exhausted` | Review the configured inference tool-call limit. |
| `inference_transport_failed_no_retry` | Inspect the recorded inference transport failure before any retry. |
| `history_no_matching_sessions` | No captured events matched this project and --since; rerun with --since first, point CODEX_HOME or CLAUDE_CONFIG_DIR at the session store, or run ahl improve . without --history. |
| `history_source_ai_memory_unsupported` | ahl improve reads native Codex and Claude sessions only; rerun with --source native, or run ahl analyze . --memory-manifest MANIFEST --authorization-file FILE for a read-only ai-memory analysis. |
| `instruction_removal_inconclusive` | Restore the missing behavioral controls before evaluating the removal. |
| `instruction_removal_rejected` | Review the failed behavioral controls before revising the removal. |
| `instruction_validation_blocked` | Review the instruction validation findings before retrying. |
| `isolation_qualification_unverified` | Verify the required isolation qualification before retrying. |
| `missing_or_duplicate_trials` | Review the trial records and restore complete, unique trial coverage. |
| `evaluation_manifest_changed` | Review the changed evaluation manifest before starting a fresh evaluation. |
| `grader_changed_after_trials` | The grader or acceptance boundary changed after trials started; the comparison cannot support adoption. Run fresh trials under the new grader as a new comparison. |
| `trial_split_mismatch` | Review the trial records and manifest partitions before starting a fresh evaluation. |
| `no_data` | Run ahl audit . for a zero-model-cost static convention check; ahl improve may incur model costs. |
| `offline_configuration_forbids_model_execution` | Use an offline operation or explicitly configure a supported runtime. |
| `offline_sandbox_unavailable` | Provide the required offline sandbox capabilities. |
| `permission_qualification_not_met` | Review the permission qualification and restore the required isolation. |
| `precision_target_infeasible` | Review the requested precision target and available budget. |
| `preflight_budget_exhausted` | Review the preflight budget before retrying. |
| `prior_inference_request_unresolved_no_retry` | Resolve the earlier inference request before issuing another. |
| `no_change_evidence_insufficient` | The analysis could not support any repair; this does not show the harness is fine. Follow the ranked next_steps in the receipt, or read analysis-brief.json missing_evidence. |
| `no_change_harness_supported` | Positive verified-outcome evidence covers only the examined behaviors; review which behaviors were examined before extending the conclusion. |
| `restricted_process_protocol_unqualified` | Restore the required restricted-process qualification. |
| `responses_inherit_env_unsupported` | Remove --inherit-env when using the Responses runtime. |
| `select_one_runtime_with_agent` | Several supported runtimes are installed and the invoking host is unknown; ask the user once to choose, then rerun the same command with --agent codex or --agent claude. |
| `shared_trial_budget_exhausted` | Review and increase the shared trial budget if appropriate. |
| `target_filesystem_read_only` | Use a separately authorized writable execution context or keep analysis read-only. |
| `status:applied` | Review the recorded deployment and its rollback information. |
| `status:blocked` | Review the reported prerequisite before retrying. |
| `status:budget_exhausted` | Review the recorded budget before retrying. |
| `status:conflict` | Review the recorded conflict before retrying. |
| `status:evaluation_failed` | Review the failed evaluation records before retrying. |
| `status:execution_error` | Review the recorded execution error before retrying. |
| `status:incomplete` | Review the incomplete run and missing evidence. |
| `status:inconclusive` | Review the incomplete evidence in the private receipt. |
| `status:interrupted` | Review the interrupted run before starting another. |
| `status:invalid_input` | Review and correct the reported input error before retrying. |
| `status:invalid_proposal` | Review the invalid proposal and its recorded validation. |
| `status:legacy_attempted` | Review the recorded legacy operation before retrying. |
| `status:no_change` | Review the evidence and proposal before deciding whether another candidate is needed. |
| `status:rejected` | Review the recorded evaluation and rejection evidence. |
| `status:requires_intervention` | Review the recorded intervention requirement before retrying. |
| `status:setup_error` | Review and correct the recorded setup error. |
| `trial_budget_requires_two_preparation_and_six_paired_trials` | Raise the trial limit to at least 8 in ahl.json limits.trials. |
| `unresolved_operation_requires_hook_contract` | Supply the required hook contract before considering the operation. |
