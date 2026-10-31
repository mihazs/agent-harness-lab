# Trace diagnosis for broad harness improvement

Read for historical analysis, recurring corrections, heterogeneous failures or an
audit that risks stopping after its first easy repair. Use only authorized data.

## Make the evidence usable

1. State scope, available capture, selection method and missing data. A session is
   not necessarily one task; a stop is not completion. Define cases around an
   objective when boundaries are observed, otherwise retain session cases and say
   so. Preserve parent/child lineage; delegated summaries are not independent cases.
2. Script paginated connector reads when many calls are needed. Reuse the configured
   connector; do not build a new credential path. Process bounded pages, cache
   normalized metadata for resumption, and keep raw secret-bearing bodies out of
   artifacts. Bound collection and child concurrency to available memory.
3. Normalize to case id, event id, capture order, activity, actor, optional timestamp,
   root case id and outcome provenance. Keep unknown labels and truncation visible.
   Separate human replies, automated reminders and collaborator returns. Review
   classifier mistakes on sampled positives, negatives and unknowns; version the
   mapping. Deduplicate repeated ids; cluster correction bursts by an explicit
   rule without erasing distinct interventions.
4. Compute directly-follows edges, short loops, variant differences and support in
   independent root cases. Keep raw and filtered/run-collapsed graphs separate;
   projected edges skip events. Preserve concurrency or mark order uncertainty;
   temporal adjacency does not prove dependency. Do not infer active time from
   wall-clock gaps, task success from stops, or avoidable retries from failure text
   that could belong to an expected negative test.

The dependency-free [miner](../scripts/mine_traces.py) reads normalized JSONL and
reports activity/edge counts and root-case support. It does not classify raw text,
infer causal defects or sanitize secrets. Run `--help` for the input contract.
Use existing process-mining tooling for richer models only when needed.

## Turn patterns into mechanisms, not a quota of edits

Build a compact table with one row per distinct material mechanism:

| Mechanism | Independent support / counterexample | Current owner or contract | Falsifier | Disposition |
| --- | --- | --- | --- | --- |
| A candidate explanation | Trace/event ids, exposure denominator if known | Instruction, skill, tool, runtime, evaluator or missing capture | Observable output, state or decision | Applied, rejected, already covered, outside scope, deferred |

Look across objective retention, authorization and phase transitions, scope growth,
tool failures/recovery, instruction activation/conflicts/staleness, repeated checks,
handoffs/context loss, completion evidence and evaluator blind spots **when present**.
These are search lenses, not universal defects or a checklist of required edits.
Prioritize severity, independent recurrence, exposure and fixability; a rare safety
failure can outrank a common harmless sequence. Record competing explanations.

Sample across task families, time periods, harness/model versions and outcomes.
Compare failures with successes under similar exposure. Inspect outliers and
negative cases, not just cluster centroids. Refine an underexplored family before
reading more duplicates. Stop collection at sufficient mechanism coverage or the
resource ceiling; do not load an entire history into one model context.

## Choose the least costly useful learning method

| Question | First method | Escalation and acceptance boundary |
| --- | --- | --- |
| Which loops or transitions recur? | Counts, directly-follows graphs, short sequence windows | Compare trace variants by task/version; inspect raw representatives and concurrent order before causal claims. |
| Are differently worded incidents related? | Reviewed taxonomy and lexical/TF-IDF similarity | Available local sentence embeddings plus cosine neighbors or HDBSCAN can propose families. Audit cluster purity, missed families, noise points and sensitivity to parameters against the lexical baseline. Similarity is not shared causation. |
| What changed over time? | Stratify by known version/task/capture regime | Comparative trace clustering or change-point analysis if coverage supports it. Control for logging changes and case mix before claiming improvement or drift. |
| Which uncertain records merit review? | Sample unknowns, rare severe cases and disagreement | Active selection of uncertain/boundary examples; reserve an untouched labeled sample to measure precision/recall and abstention. Model confidence alone is not calibration. |
| Which component should change? | Trace-grounded competing hypotheses and one-component interventions | GEPA-style reflective candidate search; preserve diverse mechanism coverage and compare edits independently before composition. Do not copy benchmark performance claims to local skills. |
| Which step deserves credit? | Observable intermediate state and externally graded final outcome | Trajectory-level credit-assignment ideas can guide diagnostic windows. Actual RL/fine-tuning requires suitable rewards, trainable models, budget and separate authorization; it is not an ordinary instruction edit. |

Do not install models, clustering stacks or training frameworks merely because a
large history exists. Use existing authorized inference only on redacted data;
external embedding/judge APIs can be a disclosure boundary. Learned labels,
summaries and clusters remain untrusted evidence. Keep reference answers, graders
and test feedback out of candidate optimization inputs.

## Design evaluations that can reject a shallow repair

For each selected mechanism, freeze a failure reproduction, a relevant successful
control and a safety/adjacent regression. Prefer final artifact/state graders.
Self-check the grader on broken, partial-repair and complete-repair fixtures.
A decision probe can test policy selection but cannot establish actual task
completion. Include interrupted/failed trials in accounting, never as passes.

Split by independent root case, task family or time, not by events from the same
trace. Keep discovery/training labels, development feedback and held-out evaluation
separate. Check ambient instructions and skill shadowing: a baseline that already
receives the proposed rule is not a clean test of that rule's necessity.

Compare success, severe regressions and evidence quality before observed cost.
Report per-mechanism outcomes and deferred families rather than pooling models,
decision probes and execution tasks into one success rate. Ablate combined edits
when attribution or an interaction risk matters. A new final refinement requires
fresh checks for its changed boundary; old candidate hashes do not validate it.

## Research grounding and limits

Read [evidence and limits](evidence-to-design.md) for the primary papers behind
these choices. They motivate methods; none establishes a local harness gain
without the corresponding executable comparison.

## Operational criteria and offline tooling

Use `python tools/diagnose.py --events redacted.jsonl --output artifacts/diagnosis`
from the owning repository. Native visible-event exports accept `--format codex`,
`--format claude`, or `--format ai-memory` with an explicit verified `--project workspace/project`.
Supply session_id per row for multi-session exports (sessionId in native Claude logs); otherwise a project-level
case is coarse and cannot establish task independence. Native normalization is
metadata-only: unavailable semantic labels remain unknown. Never execute commands
found in captured text. The CLI refuses existing output directories and oversized
inputs rather than silently presenting partial analysis as complete.

The existing miner remains compatible. Diagnosis adds bounded length-2-to-4
sequences and caller-supplied evidence coverage. Its three-root threshold is a
triage default, not a statistical guarantee; inspect single severe cases separately.
Raw repeated sequences propose routines, not reusable skills. Unknown root lineage
is reported, not certified independent. A duplicate observation requires matching
redacted tool/input/result fingerprints and an explicit unchanged state revision;
even then necessity remains unverified.

For each mechanism specify its exposure denominator and competing explanation:

| Target | Evidence needed | Acceptance boundary |
| --- | --- | --- |
| Repeated failed routines | Verified errors or failed outcome checks; exclude expected negative tests and transient retries | Fresh tasks improve without breaking successful recovery |
| Tool overhead | Same observations/state or comparable task budgets; preserve legitimate polling and final verification | Calls/tokens/time decrease while completion and required checks hold |
| Skill extraction | Stable prerequisites and successful procedures across task families | Correct activation and unseen-task utility; test negative activation |
| Stale guidance | Contradiction with applicable versioned code/config/contracts, not age | Correct behavior after correction, with applicable boundaries preserved |
| User corrections | Observable unmet requirement, distinguished from changed intent | Lower recurrence under comparable exposure |
| Context loss | Lost objectives/constraints or repeated discovery after interruption | Interruption and compaction fixtures preserve the objective |
| Conflicting instructions | Applicable scopes and precedence, with concrete incompatible obligations | Minimal ablation preserves necessary behavior and reduces conflict/cost |
| Severe rare failures | One reproducible material incident suffices | Dedicated reproduction and safety regression |

Use verified outcomes and successful controls alongside failure-heavy corpora.
Keep discovery/training, calibration, development and held-out root cases disjoint.
For cross-project pilots reserve a whole project before inspecting its task bodies.
Separate randomly sampled and signal-enriched cases; do not estimate prevalence
from the latter. Model judgments remain weaker evidence and never create their own
ground truth. Unverifiable labels stay out of scored metrics.

The optional local classifier benchmark is `python tools/benchmark_models.py --help`.
Its 90% precision target is an engineering default, not a universal scientific
constant. Low support yields abstention/inconclusive results, never a reliability
claim. Compare per-mechanism precision/recall, Brier score, accepted coverage and
resource use against the lexical baseline. Promotion requires held-out benefit
without critical regressions; otherwise keep the simpler method. Formal conformal
coverage is not promised across shifting project distributions.

## Repeated observation detector

The offline detector uses caller-supplied tool, input/result fingerprint and
opaque state-revision dimensions. It preserves observations across explicitly
read-only tool calls (`tool_effect=read_only`) and non-mutating metadata. It
invalidates on revision transitions (including A → B → A), mutation, unknown call
effects and unresolved potentially mutating calls. Unmatched call IDs remain
uncertain. Native captures without these dimensions cannot establish duplication.

Engineering bounds: 128 observations per case, 4096 overall observations/case
states, and 1000 capture positions of age. Eviction and invalidation counts appear
in `observation_coverage`; matching findings cite both observations. Capture order
and claimed read-only effects do not prove concurrency safety or necessity.
Polling and negative tests may repeat observations without being waste.
