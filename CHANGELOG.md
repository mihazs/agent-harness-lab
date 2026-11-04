# Changelog

## 0.86.0

### Minor Changes

- 83bb4ec: Add a `diagnostic-corpus` input profile to `ahl benchmark`. It reads a digest-checked `CorpusStore.export_development` manifest, keeps the corpus fitting/development split and declared root groups, and never re-splits or admits protected partitions. Exposure-retired protected items must carry the export's retirement reference and are counted by declared partition in the report. Every report states label provenance counts; default JSONL rows are labeled `adapter_smoke` and never count as reviewed. Corpus reports are private and descriptive only, and they grant no gold-label, certification or classifier-selection authority.

## 0.85.0

### Minor Changes

- 2bdf76e: Derive the isolation element of the recommendation-only `promotion.policy.decide` API from a live qualified implementation-prerequisite scope (`prerequisite_scope`) instead of a caller boolean. A scope that is absent, lost, oversized, copied, expired or not fully qualified reports `isolation: unknown` and blocks. The legacy `isolation_verified` keyword is now declaration-only: it is recorded under `declared_prerequisites` and can no longer satisfy isolation, so decisions only become stricter. Other certification prerequisites, automatic promotion and adoption authority are unchanged.

## 0.84.0

### Minor Changes

- b67732d: Add an explicit GitHub selection v6 creation-time window for conversation and inline review comments, and record previously present in-window comments missing from a complete recapture as unconfirmed `deleted_or_unobservable` tombstones; a comment listed again after a tombstone becomes a new present revision bound to it. Windows are validated and fingerprinted; failed or truncated captures produce no tombstones; earlier selection versions keep their fingerprints and behavior.

## 0.83.0

### Minor Changes

- e4ce919: `ahl experiment --preflight` now reports a descriptive `precision-sample-size-v1` plan: the minimum total of independent roots, with one sufficient allocation across families, that the frozen weighted-root Hoeffding method needs to meet the plan's half-width target and criteria, or an explicit infeasible reason for the task cap, trial ceiling, frozen cohort membership or a numerically unrepresentable target or alpha. Repeats never count as roots, and the plan never changes budgets, banks or adoption.

## 0.82.0

### Minor Changes

- ab3cf49: Add a declared Claude Code 2.1.317 project memory inventory to offline Git captures. It fingerprints root and nested `CLAUDE.md`, `.claude/CLAUDE.md` and `CLAUDE.local.md` files, plus bounded `@path` imports up to the documented four hops, with missing, cyclic, external, out-of-cwd, depth-limited and Markdown-ambiguous imports flagged explicitly and unresolved references aggregated per importing file. AGENTS.md shadowing is labelled as resting on the assumed default setting. Rules, on-demand subdirectory files and managed or user memory stay unknown, observed loading stays unknown, and nothing outside the checkout is read.

## 0.81.0

### Minor Changes

- 0c4dd72: Add an offline Git/GitHub commit join over cached checkpoints. It keys on the declared GitHub repository fingerprint plus the exact full lowercase SHA, reports sources without a shared repository identity as unlinked, force-pushed commits as stale and missing identity as unknown, and emits only a private candidate receipt with no outcome or authority.

## 0.80.0

### Minor Changes

- cf21240: Add signed closed-input delegation for conditional final-bank requalification: `ahl bank-review trust-delegation-reviewers`, `allocate-delegation` and `prepare-delegation` prepare an unsigned request for an external reviewer, and a live, process-bound closed domain joins each owned construction stage to its observed inputs, native completions and publication. Renewal issues one consumed-before-reservation capability and never refunds it. Focus, source context, memory and incident inputs are rejected under this profile, and its private profiles are excluded from public benchmark export. Provider identity, pretraining and external exposure stay unknown; no certification, application or adoption authority is granted.

## 0.79.0

### Minor Changes

- ec8245f: Route installed harness self-improvement skill invocations through the existing project controller and preserve its configuration, authorization, budget and completion contract. Deny accidental nested controller or trial entry from managed preparation children.

## 0.78.0

### Minor Changes

- 325b2c6: Configure existing controlled Responses settings and bounded worker references once in ahl.json, then resolve them through plain improvement and watch. Preserve offline policy, explicit overrides and legacy runtime shapes; report local readiness without authenticating or launching a runtime, and stop further transport when admitted references change.

## 0.77.0

### Minor Changes

- 6d77ddd: Bind structural and behavioral change proposals to one immutable private review contract, with exact file inventories and a shared identity carried into guarded structural application receipts.

## 0.76.19

### Patch Changes

- 9c46288: Enforce the workflow prerequisite receipt bound on every save: measure the cached compact canonical encoding, discard oversized receipts, stop later admissions and report the encoding with the counted bytes.

## 0.76.18

### Patch Changes

- a52d494: Report a committed contract repair truthfully when recording its result fails or is interrupted: read back the journal and exact expected inventory, keep the deployment and rollback identity, and require intervention when the target changed.

## 0.76.17

### Patch Changes

- fca4a15: Run existing local lexical retrieval in analysis when a reviewed-declaration corpus and query are explicitly supplied; retain private untrusted results and actual method receipts.

## 0.76.16

### Patch Changes

- 3b7c194: Select supported analysis interpretations from structural evidence prerequisites and report complementary methods with shared-input dependence and unqualified raw outputs.

## 0.76.15

### Patch Changes

- fd1ee23: Run shared directly-follows process mining during analyze and improve, expose executed methods and evidence coverage in private receipts, and report aggregate progress on stderr. Add the analysis alias while preserving JSON stdout and behavioral application gates.

## 0.76.14

### Patch Changes

- Expose authenticated fixed experiment schedules for private inspection without changing evaluation authority.

## 0.76.13

### Patch Changes

- Report authenticated fixed experiment history coverage without treating unsealed progress as evidence.

## 0.76.12

### Patch Changes

- cd63e89: Expose the selected executor's controller-observed harness input digest in private live source rechecks, preserving unknown effective loading and replay boundaries.

## 0.76.11

### Patch Changes

- 4d08f26: Stop further controlled inference after a grader permission audit becomes violated or unknown, preserving separate artifact outcomes and existing schedule records.

## 0.76.10

### Patch Changes

- 81fcaa1: Reject incomplete sentence-embedding input coverage before encoding through a checked unprompted Transformer and Pooling pipeline. Preserve saved encoder and real model context limits without silently scoring truncated text or inventing chunk aggregation.

## 0.76.9

### Patch Changes

- 5119c78: Retain all extracted TF-IDF training features and reject unsupported or exceeded NLI model contexts instead of silently truncating evidence or hypotheses. Long inputs still require a local model with sufficient supported capacity.

## 0.76.8

### Patch Changes

- 336047b: Read every classifier benchmark example and complete evidence text by default, with an optional explicit user-set example limit. Preserve malformed-input and split validation.

## 0.76.7

### Patch Changes

- 8404487: Evaluate explicit frozen incident-triage policies on held-out declared roots with class-wise errors, abstentions and policy-specific decisions. Preserve unverified independence and label authority; descriptive classifier accuracy does not grant certification.

## 0.76.6

### Patch Changes

- f85eed6: Use explicitly pinned project task banks for a single cooperative improvement comparison, respecting the shared preparation/trial budget and preserving recommendation-only results.

## 0.76.5

### Patch Changes

- e904932: Analyze an explicitly scoped, pinned ai-memory session through paginated read-only MCP, with faithful lifecycle activity and explicit diagnostic coverage and snapshot limitations.

## 0.76.4

### Patch Changes

- 422e6a5: Report privacy-safe collection stages and evidence counts on stderr while preserving JSON receipts on stdout.

## 0.76.3

### Patch Changes

- 6e69595: Recognize ai-memory pre/post tool hooks as call/result activity while preserving unknown tool identities and outcomes when structured metadata is absent.

## 0.76.2

### Patch Changes

- 04e2601: Use one Python 3.14 CI version and collect both test suites with parallel pytest workers, including installed-layout checks. Keep pytest tooling in optional test dependencies.

## 0.76.1

### Patch Changes

- Remove implicit analysis size, read, retention and reporting ceilings; preserve explicit caller limits, scope checks, private metadata handling and truthful upstream incompleteness. Admit complete read-only preparation inputs while retaining controlled execution and adoption safeguards.
- Explain command inputs, outputs, defaults and side effects throughout CLI help, and distinguish proposer-declined, unchanged-candidate and unsupported-evaluation no-change outcomes.

## 0.76.0

### Minor Changes

- 3b5cef1: Add an opt-in root AGENTS instruction protocol that injects frozen content into actual controlled Responses requests and binds owned request facts to worker and comparison custody. Keep model identity, semantic adherence, certification and adoption authority unverified.

## 0.75.0

### Minor Changes

- 668b273: Persist realized comparison schedules and bind reserved inference checkpoints to the actual issuing process, role, trial and cumulative budget before transport. Preserve spent reservations on interruption, reject copied or inherited clients, and keep cold resume and provider reconciliation unsupported.

## 0.74.0

### Minor Changes

- 65c2d18: Connect controlled improve and watch to an explicitly selected private evaluation store. Automatically record owned proposer, author and feedback handoffs, refuse managed freshness for actual constructed scientific assets, and preserve unknown initial parentage, shared control boundaries and unmanaged defaults. Stage completion supplies no certification or adoption authority.
- 65c2d18: Bind explicitly selected construction stores to the existing repeated improvement controller. Reject stores inside private comparison inputs before metadata writes, retain immutable request and shared trial limits, and preserve unqualified single-root outcomes.

### Patch Changes

- 65c2d18: Bind owned author output once inside the actual role before returning it. Reject re-registration and later artifact rewrites before registration or external publication, retaining unresolved exposure without a refund.

## 0.73.0

### Minor Changes

- d4ef0ae: Add an opt-in fixed repeated-bank path to improve, with fresh private Responses execution custody, sealed history and verified decision receipts. Keep certification and automatic adoption unavailable.

## 0.72.0

### Minor Changes

- a77398f: Execute conditional finite-bank sequential diagnostics through fresh paired root blocks, with exact conservative inference and controller-owned inspection history. Preserve fixed-final defaults and keep scientific assumptions, adoption and future-model performance unverified.

## 0.71.0

### Minor Changes

- 8d838d6: Qualify a bounded Linux restricted-process startup protocol from authenticated worker readbacks and owned native completions. Reject audited workflow recommendations with missing facts while keeping full certification and model identity unverified.

## 0.70.0

### Minor Changes

- 4658ab1: Report bounded implementation prerequisites from the public controlled workflow's actual owned worker and fresh-grader receipts. Keep certification authority unverified, permission violations visible and automatic promotion disabled.

## 0.69.0

### Minor Changes

- bf1ff81: Add purpose-specific signed final-bank review with actual native control audits and conditional qualification consumed by protected diagnostic access. Preserve explicit external-exposure unknowns and the remaining certification and adoption gates.

## 0.68.0

### Minor Changes

- e289aae: Add optional offline OpenSSH key approvals to private regression review under an explicitly installed host public-key policy. Bind approvals to frozen case evidence and current policy, preserve unsigned review compatibility and historical verdicts on revocation, and retain development-only admission without scientific certification.

## 0.67.0

### Minor Changes

- 14eb7d8: Report exact frozen-plan root-level precision before repeated execution, with an optional pinned half-width target that blocks infeasible dispatch without changing legacy plan identities or fixed-final inference.

## 0.66.0

### Minor Changes

- d6efb7a: Add an explicit owned development-variant lane using existing controlled authoring and the access ledger. Bind actual input/output bytes, preserve conservative may-derived exposure through transitive variants, and block managed freshness while construction is pending or unresolved. Raw unconfigured authoring remains outside this lane; no independence or certification authority is introduced.

### Patch Changes

- f1b048c: Bind owned author output once inside the actual role before returning it. Reject re-registration and later artifact rewrites before registration or external publication, retaining unresolved exposure without a refund.

## 0.65.0

### Minor Changes

- 1d686ac: Resolve project runtime, harness scope and budgets before improvement or monitoring, and emit private operation-level change plans. Add explicitly requested minimal skill-header BOM repairs with verified journaled application and public rollback. Recheck configuration and deadlines at mutation checkpoints, safely recover interrupted writes, preserve foreign edits, and prevent inherited child cleanup from releasing the parent's project lock. Behavioral promotion and native activation remain unverified.

## 0.64.0

### Minor Changes

- 8b18ffd: Add explicit data-only Promptfoo and open-source OpenAI Evals subset imports with private frozen snapshots, semantic conversion reports and unqualified regression proposals. Preserve foreign scores and references as reported claims; require separate reproduction and audited oracle evidence before existing development-bank admission.

## 0.63.0

### Minor Changes

- Add `ahl update` for release installations, retain installation locations and selected skill directories, and serialize installation transactions to keep launchers and receipts consistent. Bundle the checksum-verifying installer and document one-line installation.
- 2bef4b9: Capture explicitly selected controller sources from actual pinned offline workers and fresh artifact graders. Exact live capabilities support bounded runtime and grader-attempt projections; private journals preserve declared metadata and candidate relationships without replaying verification from serialized claims.

### Patch Changes

- Make installation and project usage prominent in the README and document newcomer guidance from public sources.
- Use focused bug and feature issue forms and a shorter pull request template with explicit verification and privacy guidance.
- Keep managed access retirement and case publication under one store-writer lock so a competing writer cannot strand the winning reservation before dispatch.
- b5a0c1b: Bind optional live source receipts to the existing permission monitor's privately owned launch and wire completion. Preserve declared journal replay, exact grader command and independent permission qualification. Retire source-session authority on normal and exceptional exit, including saved Python-context reuse, and bind live authority to its issuing process.

## 0.62.0

### Minor Changes

- 4f13fdd: Connect private one-shot protected-diagnostic reservations and durable exposure accounting to actual controller dispatch. Preserve unverified qualification, reject unsupported certification claims, and refuse known retired-asset reuse without preventing ordinary development comparisons.

### Patch Changes

- f2516f7: Clarify permission-audit inference stopping: proposer, evaluator-author and executor command failures stop continuation; grader audit failures block final qualification while remaining trials can still run.

## 0.61.0

### Minor Changes

- 3941676: Add an opt-in Linux x86_64 permission-result audit for pinned, quota-backed isolated workers, with private bounded observations and qualification gates separate from artifact outcomes.

## 0.60.1

### Patch Changes

- d5c2884: Separate task-bank review declarations from certification authority. Keep structural validation and descriptive export available without falsely marking self-labeled banks certification-eligible or their roots verified independent.

## 0.60.0

### Minor Changes

- 290366c: Add opt-in hash-only ai-memory MCP selected-page captures through the existing source registry and journal, with shared bounds, scoped readbacks and explicitly partial retrieved-context coverage.

## 0.59.1

### Patch Changes

- 74b90cf: Reject observed payload or declaration changes during normalized local export capture before advancing source checkpoints. Readbacks recheck scope and enablement before opening the payload, preserve stable v1–v3 snapshot identities, and make no complete-history or execution-authority claim.
- 74857fe: Run the existing wheel and archive validation tasks concurrently after serial package preparation, requiring both layouts to pass under the unchanged offline-packaging CI job.

## 0.59.0

### Minor Changes

- 408e5d9: Add a bounded private diagnostic annotation corpus API with immutable review journals, blinded packets, disagreement/adjudication history, stale-client protection, protected-split alias checks and explicit exposure retirement. Exported annotations remain operator-attested declarations without gold-label, classifier-selection or executable task-outcome authority.

## 0.58.0

### Minor Changes

- Add frozen, bounded trajectory assertions over controller requests, denials, dispatches and observed results. Combine three-valued action compliance with independent artifact grades, retain critical violations and unknown required evidence, and expose private descriptive reports without reconstructing controller authority from saved JSON.

## 0.57.0

### Minor Changes

- Retain bounded private assistant text and refusal observations separately from tool actions, with explicit protocol, quota, omission and visibility metadata. Preserve malformed or denied-response observations without treating text as task success or proof of external delivery. Enforce UTF-8 retention bounds and mark lossy invalid-Unicode observations unknown. Keep execution quotas, worker policy and automatic-adoption restrictions unchanged.

## 0.56.0

### Minor Changes

- Execute reviewed corrected-oracle qualification before rerunning preservation comparisons. Freeze original harness inputs, preserve prior cohort membership and rules, run fresh historical-pass and repaired-case phases, bind actual sealed evidence into a new cohort manifest, then execute both original comparison arms under the corrected oracle. Retain interrupted attempts and historical evidence after revocation, reject input overlap before writes, enforce aggregate limits and one deadline, and block public export of private qualification records. Preserve version-1 rerun compatibility and recommendation-only behavior.

## 0.55.0

### Minor Changes

- Add opt-in guarded development search using actual controller executions and freshly verified preservation receipts. Bind authorized patches to prepared harness identities, preserve charges and genuine run custody across interruption, revalidate cached results and require fresh paired confirmation before a development recommendation for review. Enforce early input-entry limits and deadlines through final verification. Keep callback results unverified and certification, automatic adoption and corrected-oracle requalification disabled.

## 0.54.0

### Minor Changes

- Generate private JSON, Markdown and HTML reports from freshly verified controller receipt references. Retain qualification provenance, preservation endpoints, losses and recoveries, intervals, assumptions and unknown observations. Recheck current authority before writing output, escape untrusted report content and refuse public export regardless of publication labels. Keep optimization integration, corrected-oracle requalification and adoption authority pending.

## 0.53.0

### Minor Changes

- Persist bounded private analysis receipts bound to current controller evidence, frozen qualification, paired analysis and the analysis engine. Reopen authority and recompute results when verifying a receipt, with explicit regeneration after engine changes. Add receipt creation and verification CLI commands and prevent controller/cohort data from being publicly exported through spoofed publication labels. Receipts confer no adoption authority; optimization/report integration and corrected-oracle requalification remain pending.

## 0.52.0

### Minor Changes

- Add conditional paired-root preservation endpoints and descriptive loss/recovery reporting for frozen cohorts. Bind numeric criteria to exact manifest bytes and allocate error across primary, family and required cohort claims. Preserve missing observations, critical-invariant vetoes and legacy plan behavior. Keep cohort-enabled promotion blocked pending bound decision and optimization receipts.

## 0.51.0

### Minor Changes

- Freeze preservation-cohort membership from reviewed prior controller executions and bind it to optional experiment plans and private history. Preserve legacy plan digests and trial IDs, enforce current task/oracle and exposure checks before dispatch, and keep favorable cohort decisions blocked while preservation analysis and decision-receipt integration remain pending.

## 0.50.0

### Minor Changes

- Add an exposed paired adversarial development bank with separate completion and final-artifact effect grading. Bind frozen behavior policies into private experiment history, reject verified critical candidate effects, and block recommendations when required observations are unknown. Preserve legacy history interpretation and reject protected-split relabelling of public development policies.

## 0.49.0

### Minor Changes

- Record bounded private controller observations for Responses tool requests and results, including denied requests and missing visibility. Preserve existing execution quotas and offline history admission; observations alone do not establish safe behavior.

## 0.48.0

### Minor Changes

- Apply reviewed grader corrections to immutable private bank revisions, preserving original approval and revocation chains and durably invalidating the old oracle first. Execute fresh complete baseline/candidate reruns from recorded plans under the same pinned runtime and one deadline; retain interrupted attempts, exact generated run identities and honest legacy identity limits. Add explicit apply/rerun commands, historical-plan inspection, subset export and real source/installed sandbox controls. Keep certification, native identity, causal benefit and automatic promotion unqualified or disabled.

## 0.47.0

### Minor Changes

- 4010413: Bind controlled-bank comparisons to actual current bank approval, selected task policy, materialized fixture identities and grader versions. Recheck inherited revocations through execution and guarded analysis, preserve legacy unbound history bytes, and retain immutable evidence when authority becomes unavailable. Validate the boundary with native controls and real pinned replay/admission/comparison workers; correction application remains separate work.

## 0.46.1

### Patch Changes

- Fix task-bank exports to relative output directories by using one absolute destination for fixtures and grader controls. Reject parent traversal before creating output, preserve existing destinations and retain symlink/source-overlap checks.

## 0.46.0

### Minor Changes

- Add private frozen grader-correction proposals, controlled old/replacement audits and explicit operator artifact review. Preserve failed and retryable observations, reject forged or changed evidence, and keep grading defects separate from historical task failure. Reuse the existing audit core and retain bank/root/exposure metadata; approved immutable bank replacement remains separate work.

## 0.45.0

### Minor Changes

- Add private controller-owned history for actual pinned offline comparisons and monotone quarantine of shared grader dependencies. Preserve sealed historical evidence, invalidate affected comparisons across task subsets, reject incomplete or forged results, and require current oracle history for certification promotion. Retain legacy mathematical analysis with unknown validity. Positive grader correction approval and immutable bank replacement remain separate work.

## 0.44.1

### Patch Changes

- Bind repeated experiment plans to the actual complete grader bundles, grading arguments and artifact selection before execution. Changed oracles require a new plan and fresh baseline/candidate trial identities. Preserve descriptive legacy manifests; expose a Task command for computing the oracle pin. Historical comparison invalidation remains separate work.

## 0.44.0

### Minor Changes

- Add explicit private admission of controlled, operator-reviewed regression cases into immutable development-bank revisions. Preserve sealed raw/materialized evidence and external grader controls, bind accepted markers and inherited revocation, reject stale/duplicate/orphaned admissions, and retain legacy bank behavior. Native provenance and independent roots remain unverified; oracle correction and comparison invalidation remain separate work.

## 0.43.0

### Minor Changes

- Add private operator-attested source-lineage review over exact scoped cache snapshots, with immutable decisions, stale-cache handling, conservative components and explicit independence/derivation conflicts. Reuse regression journal persistence without changing its event bytes. Keep native freshness, independent roots, task outcomes and bank eligibility unverified.

## 0.42.0

### Minor Changes

- Add versioned bounded lexical grouping receipts and fixture/prompt exposure checks to task-bank validation. Detect normalized copies under distinct declared roots, preserve negation and ordering, reject ambiguous JSON, freeze grouping revisions in exported manifests and keep lineage/independent-root claims unverified.

## 0.41.0

### Minor Changes

- Add a private controller-owned regression review lifecycle with exact declaration copies, real offline reproduction, sealed grader/artifact evidence and event-bound operator attestations. Preserve interrupted and unqualified attempts, reject stale/self/session-only positive reviews, quarantine tampered evidence, and bound journal capacity and encoded events before dispatch or writes. Keep bank admission, publication, authenticated independence and historical verification disabled or unestablished.

## 0.40.0

### Minor Changes

- Add explicit offline regression replay over hash-bound harness, configuration, memory and recorded task-start snapshots. Audit draft controls, then compare recorded/minimized fixtures with fresh A/A and A/B workspaces under one absolute deadline and fixed resource profile. Retain graded outcomes, artifact hashes, earlier-valid evidence and distinct instability/defect dispositions in private descriptive receipts without authenticating history, establishing causality or admitting tasks automatically.

## 0.39.0

### Minor Changes

- Audit explicitly supplied regression drafts against frozen broken, partial, complete, alternative-valid and superficial artifact controls in the offline sandbox. Bind proposal/task/control hashes and runtime identity, require aggregate resource profiles, and retain private typed receipts under one parent deadline. Keep historical reproduction, independent review, bank admission and automatic promotion unverified or disabled.

## 0.38.0

### Minor Changes

- Add a bounded regression reproduction proposal validator and read-only CLI. Record exact declared snapshot/source identities, preserve missing evidence and distinguish task failures from recovered errors and environment/grader defects. Receipts never verify reproduction, admit tasks, authorize publication or promote changes automatically.

## 0.37.0

### Minor Changes

- Extend explicit Responses execution to controlled improve/watch preparation roles. Give proposer and evaluator author separate minimal views and fresh histories, preserve one client/budget through paired trials and refinement, publish complete bounded authored trees atomically, and derive/freeze grader commands in the controller. Persist request reservations and ambiguous failures without native fallback. Keep recommendation-only outcomes and unverified model/lineage qualification.

## 0.36.0

### Minor Changes

- Add an explicit controller-owned Responses executor for prepared paired evaluations. Keep credentials outside offline workers, require pinned-image/cgroup/storage profiles, preserve bounded working state in controller memory between commands, enforce shared request/tool/deadline caps, and regrade final allowlisted artifacts in fresh namespaces. Synthetic transport and real-kernel state controls validate the boundary; full proposer/evaluator orchestration, live API validation and automatic promotion remain pending.

## 0.35.1

### Patch Changes

- Charge evaluator input/configuration preparation to the whole-run budget and preserve the original improve deadline across trial-stage dispatch. An expired parent or preparation budget prevents runtime probes and model launches; inherited deadlines can only narrow existing caps.

## 0.35.0

### Minor Changes

- Add opt-in offline artifact grading for cooperative Codex and Claude evaluations and improve/watch workflows. Freeze complete grader bundles before controls, require typed outcomes and declared artifacts, block unavailable boundaries before agent execution, and forward resource/image selections to both control and trial graders. Native agents remain cooperative and automatic promotion stays disabled.

## 0.34.0

### Minor Changes

- Connect explicitly selected Claude native files to private source journals with exact row revision history, declared execution metadata, message membership and current-only relationship rechecks. Share bounded native journal machinery with Codex while preserving its preview hashes, block partial/failed checkpoints, and keep derived facets and unverified execution claims distinct.

## 0.33.0

### Minor Changes

- Add an explicit pinned Claude Code 2.1.316 native file profile with scoped writers, private block/message metadata, parallel tool correlation, compaction coverage and conservative response usage. Share guarded JSONL boundaries with Codex, enforce expansion limits before allocation, preserve duplicate UUID idempotence, and withhold conflicting or incomplete native response facets.

## 0.32.0

### Minor Changes

- Journal explicitly scoped Codex roots through the shared bounded scanner, preserving per-thread membership and exact native revision history across active/archive copies. Block incomplete root checkpoints and conflicting native claims, including file-local suppressed usage ordinals, while retaining ordinary metadata-only telemetry and unknown execution outcomes.

## 0.31.0

### Minor Changes

- Add revision-bound native Codex session membership and unambiguous tool-result relationships. Recheck only through the built-in scoped reader, reject partial files and forged proof sources, preserve stale/conflicted links and unknown execution identity, and leave journals unchanged.

## 0.30.0

### Minor Changes

- Connect explicitly selected native Codex captures to shared source journals with exact revision history, declared execution metadata, coverage and checkpoint membership. Keep partial-tail previews visible while blocking their import, preserve old model declarations and derived coverage identities, and revalidate scoped selections through the full bounded capture lifecycle.

## 0.29.0

### Minor Changes

- Add explicit bounded native Codex source-root and exact thread selection with visible scan/capture limits. Filter headers before full reads, merge archive/prefix duplicates before correlation, and deduplicate response usage without hiding ordinal conflicts. Preserve unknown lineage and outcomes, shared read budgets, private paths and unchanged default discovery.

## 0.28.0

### Minor Changes

- Add an explicit Codex 0.166.0 rollout profile for private v2 telemetry. Pin the public wire schema, validate project/version/physical history boundaries, preserve partial tails and unknown execution settings, fingerprint private metadata, and count only deduplicated own-response usage. Keep default discovery and legacy adapters unchanged.

## 0.27.0

### Minor Changes

- Add explicit read-only relationship rechecks for the built-in local Git v2 reader. Verify exact current captured relationships while preserving stale revisions, missing identities, conflicts, reference-only targets and unknown execution loading. Share comparison logic with GitHub without changing its reader admission or report contract; leave sources and journals unchanged.

## 0.26.0

### Minor Changes

- Add an explicit local Git v2 profile that atomically captures scoped commit/ref/harness relationships and coverage in the shared source journal. Preserve reference-only objects, dirty snapshots, shallow and capped-history gaps, unknown execution loading and old checkpoints; keep the v1 profile unchanged.

## 0.25.0

### Minor Changes

- Expose explicitly scoped private source-journal context in analyze and doctor without collecting sources or contacting services. Report declared execution identities, coverage gaps/limits and graph counts separately from telemetry and readiness prerequisites; preserve unknown target binding, independence and capture completeness. Fingerprint untrusted labels, bound summaries with explicit omissions, and leave journals unchanged, including legacy and missing-cache cases.

## 0.24.0

### Minor Changes

- Add explicit GitHub CLI configuration and a bounded live native-relationship recheck. Admit exact native source/ID/revision relationships through a fresh built-in capture, preserve stale/missing/conflicting uncertainty and unknown independence, and keep stored candidates unchanged. Probes/status remain offline; authorization is read only from an explicitly supplied file and is never persisted. Serialized proof labels cannot self-certify imported graphs or task outcomes.

## 0.23.0

### Minor Changes

- Persist bounded, revision-bound relationship claims with atomic graph/execution metadata checkpoints. Add journal v4 with read-only migration from v1–v3, normalized export v3 and an opt-in GitHub v5 graph profile. Preserve old capture hashes and historical graphs, reject foreign/dangling references and self-certified verified/reviewed labels, and keep independence unknown pending explicit relationship resolution.

## 0.22.0

### Minor Changes

- Add explicit GitHub v4 full-SHA commit selection, per-commit file provenance and a distinct commit-comment discussion type. Reuse native PR commit identities, retain exact parent/head/base/merge relationships and unknown blob identities, and reject truncated, conflicting or changing captures without advancing checkpoints. Preserve v1–v3 scopes and request sets; add an opt-in native public-commit acceptance task.

## 0.21.0

### Minor Changes

- Add an opt-in authenticated GitHub v3 review-thread profile. Bind fixed read-only GraphQL queries to checked REST repository, PR, comment and revision identities; capture native resolved/outdated states and nullable line ranges with bounded nested pagination and stable readbacks. Preserve older source scopes and requests, explicit unknowns, partial coverage and atomic failure without inferring task outcomes.

## 0.20.0

### Minor Changes

- Add an explicit v2 GitHub selection for native timeline and CI/check evidence while preserving v1 scope and requests. Bind timeline projections and issue references to native parents/identities, retain unmapped forms as partial capture, and bind check runs/statuses to the captured PR head. CI success remains process evidence rather than a verified task grade.

## 0.19.1

### Patch Changes

- Preserve an absent or null PR merge reference as unknown while retaining the reported merged state. Do not reject otherwise valid partial metadata or substitute the current head for an unavailable merge SHA.

## 0.19.0

### Minor Changes

- Add an explicit GET-only GitHub REST source for selected issue/PR metadata, conversation and inline comments, review submissions, proposed files and commit lineage. Verify repository/native parent identities, constrain pagination and capture budgets, recheck PR heads and preserve private hash-only metadata with declared partial coverage. Approvals and merges remain process evidence rather than task grades.

## 0.18.0

### Minor Changes

- Add immutable content-free execution identity and declared coverage receipts through explicit normalized export v2, preview/status and atomic journal v3. Preserve v1 exports and v1/v2 journal history, bind metadata changes to new checkpoints and keep missing/conflicted identities explicit. Current collectors reject verified execution claims rather than trusting provenance labels.

## 0.17.0

### Minor Changes

- Add an optional declared Codex project instruction inventory to offline Git captures. Fingerprint root and nested guidance, overrides, fallbacks, shadowing and task scope while preserving unknown loading and ambient state. Unsupported versions and ambiguous native truncation remain unresolved, and discovery grants no mutation authority.

## 0.16.0

### Minor Changes

- Add an explicit offline Git metadata source with bounded private object-store copies, verified object integrity and selected immutable harness-file snapshots. Record hashes instead of private paths or commit text, ignore source execution settings and remotes, and retain unknown runtime-loading and effective-configuration state.

## 0.15.0

### Minor Changes

- Allow mutable source snapshots to return to previously observed content without treating the change as a pagination cycle. Keep native revision deduplication and opaque cursor guards, and migrate metadata journals atomically to schema 2 only after a successful import.

## 0.14.0

### Minor Changes

- Add opt-in quota-backed private storage for offline sandbox workers and graders. Verify bounded work, temporary and shared-memory volumes, seed fixtures through a private directory handoff, and export only bounded declared artifacts after whole-tree cleanup. Require explicit cgroup controls, deny HugeTLB allocation through an inherited syscall policy, and keep automatic promotion disabled.

## 0.13.0

### Minor Changes

- Add opt-in SHA-256-pinned extracted runtime images to offline executors and pristine graders. Inspect a bounded private rootfs without executing image code, bind its complete content/permission/ownership/mtime/link/xattr identity, reject unsafe files and role-data overlap, and verify integrity before and after each worker. Expose explicit image flags and a Taskfile pin command; unpinned diagnostics remain labeled and automatic promotion stays disabled.

## 0.12.1

### Patch Changes

- Fix an oracle-read bypass in the starter Python grader: sandboxed submitted code now runs in a nested namespace without oracle or grader-process access. Version the frozen graders as v2 while preserving the explicitly trusted cooperative diagnostic path.

- Add opt-in native kernel canaries in a disposable foreground systemd user scope, including real role/file/network boundaries, pristine regrading, aggregate cgroup resources and offline paired evaluation. Support source, wheel and archive layouts without persistent services or changing runtime defaults. Document per-user process-limit failures and keep unsupported tests explicit; full isolation certification remains pending.

## 0.12.0

### Minor Changes

- Add opt-in delegated Linux cgroup v2 controls for offline executors and pristine graders. Verify aggregate memory/process caps and CPU bandwidth policy before launch, attach before exec, kill the whole descendant tree and require complete accounting before attestation. Missing delegation or cleanup failure never falls back to a weaker boundary. Kernel enforcement canaries require an explicit delegated host; disk quotas, inference brokering and full role isolation remain pending.

## 0.11.0

### Minor Changes

- Add an opt-in stateless JSON MCP reader for ai-memory memory_query and memory_read_page. Require explicit scope/authentication and pinned server/tool schemas, reject mutators/global queries/stateful or SSE-only responses, bound discovery and payloads, and redact failures without retries. Reuse bounded HTTPS exchange logic with the existing GET transport. Remote CLI activation and normalized MCP journal ingestion remain pending.

## 0.10.0

### Minor Changes

- Read explicitly selected extracted ai-memory OKF v0.2 project bundles with an optional YAML extra. Preserve parsed page metadata in memory, import fingerprinted contextual evidence through atomic checkpoints, and report freshness, authorship and lifecycle claims without treating summaries as execution or review proof. Reject unsafe YAML, links, special files, nested scopes and project mismatches; never read the derived database or start servers/hooks.

## 0.9.0

### Minor Changes

- Add an explicitly authorized HTTPS GET transport for source adapters with TLS verification, same-origin/path pagination, bounded pages/records/bytes, timeouts and cooperative deadlines. Reject redirects, rate limits, malformed/partial responses, cursor cycles and credential-bearing URLs without automatic retries. Provider decoders stay explicit; no remote connector is enabled by installation or discovery.

## 0.8.0

### Minor Changes

- Add explicit offline source probe, preview, import and status commands with version-gated registry dispatch. The local normalized-metadata export connector checks declared project/scope and root identity before reading bounded record data, freezes file snapshots and imports through atomic checkpoints. Default discovery enables no sources; provider APIs and native session formats remain pending.

## 0.7.0

### Minor Changes

- Add a bounded private metadata journal with atomic record/checkpoint commits, source-scoped revision idempotency, preserved tombstones and explicit supersession claims. Reject stale or cyclic cursors, conflicting revisions and cross-project cache use; report ambiguous filesystem commits for inspection. Keep unknown capture and grading authority unchanged. Centralize reusable development commands in Taskfile and replace standalone packaging smoke commands with native unittest cases shared by source, wheel and archive layouts.

## 0.6.0

### Minor Changes

- Migrate the zero-clone staged pre-commit gate to Lefthook. Explicit guarded hook installation preserves foreign hooks and migrates the repository's legacy hook path; automatic npm lifecycle installation is disabled.
- Install Lefthook explicitly in release automation and run feature CI once per pull request, retaining all required checks and main-branch validation.

- Define strict metadata-only source, evidence and import-receipt contracts and an explicit version-gated capability registry. Preserve separate evidence planes and unknown provenance; discovery performs no source reads, plugin imports or network calls. Production connectors and checkpoint persistence remain pending.

## 0.5.1

### Patch Changes

- Keep doctor readiness checks limited to bank metadata, explicitly report unverified fixture/oracle contents, and block unsupported filesystem capabilities without mutation. Preserve full bank integrity verification for audits and evaluation, and reject unsafe or malformed bank files with bounded reads.

## 0.5.0

### Minor Changes

- 5508681: Add explicit project configuration to evaluate. Validate requested tasks, fixtures
  and oracle bundles against the configured bank, narrow harness/runtime/resource
  scope, record private effective settings and invalidate changed configuration.
  Unsupported profiles block execution. Configuration stays opt-in and existing
  invocation syntax remains compatible.
  Freeze evaluation inputs before runtime version queries, and stop dispatch when
  those queries or later work change frozen input contents or configured file modes.

## 0.4.0

### Minor Changes

- d299507: Add explicit offline fixture-command execution to paired evaluations. Executors
  receive only their fresh fixture and selected read-only harness; a separate
  offline grader evaluates bounded artifact copies with a pristine oracle bundle.
  Unsupported sandbox hosts block without host fallback. Export artifact allowlists
  with task-bank manifests. This mode makes no model calls or automatic promotions.
- 9c19d25: Add offline doctor checks, strict project configuration and preview-first init.
  Creation is explicit, atomic and exclusive; diagnostics omit private file contents
  and keep authentication and full isolation unverified. Configuration consumption
  is currently limited to doctor/init and does not authorize runtime execution.

### Patch Changes

- 0544449: Add opt-in offline sandbox regrading for task-bank controls. Extract bounded
  allowlisted artifacts into fresh read-only inputs, verify pristine oracle copies,
  and reject malformed or inconsistent typed grades. Unsupported sandbox hosts
  report blocked without host fallback. Include calculator source in its artifact
  contract; behavioral promotion remains disabled.
- bfef7d9: Reject writable sandbox workspaces that overlap read-only inputs. Bound combined
  stdout/stderr capture, clean up workers when selector setup fails, and count
  preflight work against the execution deadline. Share one wall-clock budget across
  offline grader audits and preserve completed checks on timeout or exhaustion.
- 9e8542f: Require zero production-source clones in jscpd working-tree and staged pre-commit
  checks. Remove the clone baseline and share telemetry identity registration logic
  without changing idempotence, conflict detection or retention behavior.

## 0.3.0

### Minor Changes

- d83b6a1: Add bounded development-only mechanism/search APIs, explicit skill/adapter/retrieval evidence contracts, private journaled adoption and guarded manual rollback, matched benchmark publication checks, offline package CI and isolated retrieval-policy simulators. Keep unsupported runtime integration, calibration and live certification explicit; no paid execution, automatic rollout or training is enabled.
- 248d3a9: Add frozen paired experiment plans, conservative root-level analysis, task-bank integrity and split checks, single-use certification access, bounded artifact extraction and an optional offline sandbox capability probe. Keep behavioral changes recommendation-only while isolation and independent certification prerequisites are unverified.
- a2a3d3a: Ship a reproducible synthetic starter task bank, objective grader falsification controls, safe descriptive manifest export and typed grading envelopes. Freeze complete declared grader bundles, preserve graded outcome provenance and include runtime fixture assets in wheel/archive installs. Keep task independence and live certification explicitly unverified.
- 1804bc2: Add typed v2 metadata envelopes, bounded native call/result correlation, a private idempotent ledger and outcome provenance APIs. Preserve successful evaluation controls without treating completion as correctness. Apply accepted capture limits after project filtering and report scan exhaustion.

### Patch Changes

- 82fe77e: Detect repeated observations across explicitly read-only calls with bounded state-aware caches. Persist watch attempts atomically, limit transient retries, preserve interrupted/corrupt checkpoints, and return truthful CLI exit codes for incomplete and blocked runs.
- 10c1f55: Explain that diagnosis requires a fresh output path while preserving existing-output refusal and exit code 2.

## 0.2.0

### Minor Changes

- Install the CLI and both agent skills from verified GitHub release assets without cloning. Run automatic diagnosis and improvement through one CLI, and manage versions and release changelogs with Changesets.

## 0.1.0 — 2026-10-31

- Project-owned harness evaluation and self-improvement skills.
- Dependency-free trace mining, offline diagnosis, and paired Codex evaluation.
- Optional local classifier benchmark with root-separated splits and abstention.
- Research-to-criterion references and open-source contribution guidance.

Experimental release: synthetic smoke checks do not establish
production diagnostic accuracy or cross-project reliability.
