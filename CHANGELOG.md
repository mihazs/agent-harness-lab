# Changelog

## 0.166.0

### Minor Changes

- 18016c6: Record comparable `skill_availability` bundle provenance (entry and bundle hashes, route, present or absent) on every evaluator trial, whether a skill is delivered through `skills` or `harness_paths`, with activation left unknown. Part of #11.

### Patch Changes

- 69d4773: Resolve ambiguous runtimes without `ahl.json` by a declared policy (single installed runtime, else the invoking host, else one question). The preflight plan and invocation context record the selection and state what an explicit invocation authorizes. The skill may re-dispatch once after a zero-spend preflight block whose remediation is a user choice.

## 0.165.1

### Patch Changes

- fe32521: Add a target-state application receipt: after a journaled adoption, `adopt_with_receipt` re-reads every installed path and mode, records created/updated/removed operations with the deployment id and rollback eligibility, and states that activation and behavioral effect are not established.

## 0.165.0

### Minor Changes

- ff19c28: Add `ahl sources lineage coverage`, a bounded private evidence-coverage report listing source kinds, missing data, attempted joins, contradicted and unverified reports, conflicts and stale cases, with independence left unproven.

## 0.164.0

### Minor Changes

- 6550ad2: Add triage evaluation profile v5 with descriptive Brier, reliability and risk/coverage diagnostics

## 0.163.0

### Minor Changes

- 21fdb68: GitLab selection schema 9 emits a candidate evidence graph of server-reported merge request commits, and `sources.commit_join.join_merge_request_commit` relates one exact local Git commit to that listing offline. A match is server-reported membership under a declared project fingerprint; it is not ancestry, merge result or outcome. Gitea reconciliation stays open. Part of #46.

## 0.162.0

### Minor Changes

- 35e374b: Add `ahl annotation-plan`, a frozen per-class precision/recall interval plan that reports additional annotation needs and keeps sparse or unreported classes inconclusive.

## 0.161.0

### Minor Changes

- d415f64: `ahl benchmark` now discloses projects shared by train and heldout rows and normalized texts that appear in several splits, with `--reject-leakage` to fail on either. Per-split metrics add `positives` and `positive_misses` so high accuracy cannot hide missed rare positives. Exact-copy detection does not find paraphrases or establish independence. Part of #14.

## 0.160.0

### Minor Changes

- 2ac4a62: Analyze an explicitly selected saved controller comparison in `ahl analyze` and the `ahl improve` evidence stage through the existing authenticated history checks. The route supports unbound fixed history only, rejects output and state paths that overlap the store before creating anything, and preserves conditional results, privacy and incomplete or quarantined dispositions without dispatching trials.

## 0.159.0

### Minor Changes

- 9608885: Add `sources.knowledge_origins`, an offline reader that groups knowledge pages citing a common source into one correlated origin, leaves uncited pages with an unknown origin, and keeps `Attested Computation` `executor`/`attester` fields inert. Part of #47.

## 0.158.0

### Minor Changes

- db90db0: Add immutable `PredictionRecord` registration with an append-only freeze ledger and a pure reconciliation table, so a candidate's expected effect, regression risks and falsifier are fixed before outcomes are read and wrong, untested, late or non-comparable predictions stay visible (part of #10).

## 0.157.1

### Patch Changes

- b0ce8d6: Add a synthetic change matrix that pins the terminal state, journal receipt and rollback of each surface operation the journal can apply (skill create, update and remove, instruction rewrite and removal, Claude hook create and update) and of each case that must leave the target untouched. Part of #77.

## 0.157.0

### Minor Changes

- cc9ddfa: Add an opt-in strict triage evaluation profile that refuses overlapping declared role roots and out-of-scope model revisions.

## 0.156.0

### Minor Changes

- 386ea85: Require a recorded reviewer decision for lexically near-duplicate diagnostic corpus incidents that cross partitions, so a reworded copy of a training incident cannot enter a held-out split unnoticed. Corpus export manifests now carry `near_duplicate_decisions`, so older exports no longer validate.

## 0.155.0

### Minor Changes

- be70ce1: Add offline precision, recall and false-link scoring (`sources.link_study`) for cross-source join outcomes against an adjudicated synthetic fixture, with ambiguous controls counted as false links when promoted, and pin the commit join at zero false links on seven synthetic controls. Part of #58.

## 0.154.1

### Patch Changes

- 85c1b47: Pin missing-Git and oversized-diff controls for offline Git evidence and update the source contract to say which reconciliation work remains open.

## 0.154.0

### Minor Changes

- 07e6d33: Correlate ai-memory session pages, handoffs and native Claude or Codex journals offline so one session is counted once and ambiguous claims stay separate.

### Patch Changes

- d976aa8: Pin the GitHub task-linkage acceptance case: one synthetic PR, review decision, thread, commit and issue join in the selected repository while a same-numbered foreign issue stays unlinked.
- 2ed3695: Document a per-runtime capability matrix for the pinned Codex and Claude native capture profiles, checked against the adapters' declared constants.

## 0.153.1

### Patch Changes

- 5494772: Explain how to review changed evaluation manifests and mismatched trial partitions.
- 5494772: Reject fixed-comparison results if configuration or inference settings change during execution.

## 0.153.0

### Minor Changes

- 327aac2: Anchor the audit sampling seed commitment time in a hash-chain log proof verified offline (#417). A `seed_commitment` may carry an `anchor` whose first entry covers exactly the commitment hash and whose chain ends in a caller-supplied trusted head observed strictly before the window end. Only a verified anchor drops `commitment_time_unauthenticated`; unanchored or unverifiable receipts keep it, and anchors over another hash, broken chains, untrusted heads and late times are refused.
- 2443900: Expose offline planned-campaign creation and inspection through the CLI.
- 618ec99: Add an offline immutable specification for future local campaign orchestration.
- 05d0d75: Check supplied resource contracts against experiment plan declarations without claiming authority.
- 55355d7: Collect private live completed-trial usage against one frozen comparison schedule.
- d64c829: Optionally seal completed-trial provider-reported category coverage in a versioned private history.
- 27e3cf4: Expose a private live usage summary after comparison trial custody accepts completion.
- 5a58d07: Preserve reported and observed outcome claims separately across reviewed source links, and surface contradictions without promoting unverified reports to outcomes.
- 420fab6: Explain analysis and improvement exit paths, and suggest the offline audit first when no captured data exists.
- b0c3969: Add an opt-in resource contract that requires fresh-execution and cache-scope attestations.
- 5f2a7f7: Add an opt-in frozen provider token category and scope to resource comparisons.
- f23cb25: Add an opt-in frozen timing boundary to conditional resource comparisons.
- 1dc1f07: Add opt-in private Gitea inline review comment metadata capture.
- 262b725: Add opt-in Gitea pull request commit membership capture.
- b592820: Add opt-in private Gitea pull request changed-file metadata capture.
- 21139f4: Add an opt-in Gitea identity profile that distinguishes recreated repositories and objects.
- 5e0c928: Add opt-in bounded GitLab issue discussion metadata capture.
- cf30974: Add opt-in bounded GitLab merge request closing-issue reference capture.
- 183efc0: Add opt-in bounded GitLab merge request commit membership capture.
- 50d4e99: Add opt-in private GitLab merge request diff file metadata capture.
- 10c264b: Add opt-in bounded GitLab merge request discussion metadata capture.
- cfcb916: Add opt-in GitLab merge request related issue reference capture.
- bf1645a: Add opt-in private capture of GitLab merge request reviewer state claims.
- 3c9228e: Add a private conditional resource bound using frozen related-root grouping.
- e070b00: Add opt-in offline Hindsight knowledge-page metadata capture.
- 1bef870: Add an opt-in offline Hindsight synthesized model metadata profile.
- 0a9aa12: Add opt-in offline Hindsight model reference claims.
- b5dd5a9: Add bounded offline ingestion of explicitly selected Hindsight transfer ZIPs.
- bfeacf0: Expose a private live view of provider usage bound to the admitted comparison trial.
- 46f3a92: Report ai-memory session-observation analysis as incomplete source insufficiency in the analysis receipt, caveats and next action instead of a clean observation, and pin unsupported server version rejection (#172).
- 7585aac: Add an opt-in offline MemPalace drawer result metadata profile.
- a2ed0f5: Add an opt-in offline MemPalace drawer-list page metadata profile.
- bbecf28: Add an opt-in offline MemPalace search-result metadata profile.
- 02ad5e9: Add an opt-in profile for one selected OpenCode backup message metadata claim.
- db44247: Add an opt-in bounded OpenCode backup message-row count profile.
- 017fb2e: Add an opt-in bounded OpenCode backup part-row count profile.
- deab6d2: Add an opt-in bounded profile for OpenCode export message metadata claims.
- 886aa9b: Expose optional OpenCode parent and archive claims in a separate metadata profile.
- cb43e55: Add a scoped metadata-only reader for one pinned OpenCode session export shape.
- ad704a0: Add bounded metadata inspection of an explicitly selected OpenCode SQLite backup.
- 4a77177: Allow experiment plans to pin a resource contract digest before trial collection.
- 5269c1e: Give each blocked `ahl improve` preflight outcome (ambiguous runtime, missing runtime, exhausted trial budget, stale or unsuitable bank, non-cooperative profile, runtime mismatch) its own actionable `remediation` instead of one generic sentence (#142). Reasons and exit codes are unchanged; no authorization or setting is altered.
- e3f47cc: Prepare a private unsealed resource evidence candidate from a live pinned comparison.
- 2bb3de9: Optionally bind a private pretrial resource quality policy to a new experiment plan.
- e02e7da: Bind private caller cache-state attestations to the request scope of sealed resource history.
- 6b21981: Allow project owners to freeze AHL changes with a root marker while retaining read-only analysis and audit.
- bcbf71c: Retain provider-reported cached input token counts in Responses controller usage.
- aad5d55: Optionally seal a private plan-bound resource observation in a versioned controller history.
- 22631b2: Add private skill activation signal receipts that preserve missing capture as unknown and keep trial availability comparisons intent-to-treat.
- 9fccfb7: Expose the offline static harness convention audit through `ahl audit`, with a private report and explicit unchecked coverage.
  
  Keep private audit reports out of public benchmark exports even when publication metadata is relabeled.
- 9f49c82: Summarize private live trial provider usage without claiming final totals.
- 013c2b0: Add an opt-in, coverage-aware summary of provider-reported controller usage.

### Patch Changes

- 5269c1e: Preserve explicit abbreviated CLI options when resolving project configuration.
- 5269c1e: Report invalid configured task banks as actionable preflight blocks.
- abc84a8: Recognize additional common lint and format configuration names in the private metadata-only audit.
- e2f850e: Keep instruction validation and removal dispositions scoped to the final improvement attempt.
- a2ec9d9: Add an opt-in private recheck for ambiguous Codex instruction budget boundaries.
- 10408fd: Preserve certain audit draws at floating-point boundaries and report finite-resolution limits.
- a1dd8a2: Recognize narrow declared Codex patch-hook candidates without claiming loading or enforcement.
- 4f10e2d: Limit the procedure fence cue to explicitly shell-labelled blocks.
- b90bcec: Add private bounded lexical references to instruction constraints without claiming enforcement.
- e9cce8a: Show private, digest-checked lexical constraint references in the audit CLI without claiming hook enforcement.
- d9531d1: Keep ambiguous or ineligible corpus observations out of audit point estimates.
- 6f397b8: Link sampled audit units to private local corpus review lifecycle observations.
- bc0d242: Record local audit review nonresponse without imputing labels.
- 239d2c6: Add an opt-in fixed-size audit design with an explicit hard review budget.
- 87ed6b8: Optionally replay grid audit draws against a caller-supplied frame.
- 688d010: Add an opt-in grid-based audit sampling design with explicit effective probabilities.
- 9c2871d: Identify narrow lexical pre-tool hook candidates without claiming path enforcement or execution.
- 16a8300: List declared hook event names in the private static audit while leaving execution unknown.
- 2e61592: Recheck selected instruction-budget conventions before an explicit improvement run.
- 43af92d: Expose private unweighted sampled missed-class review composition on request.
- a5f1a33: Add an opt-in private root npm test declaration observation to static audit.
- 57376d0: Expose private exact lexical permission-deny candidates without claiming effective enforcement.
- 0f94f0c: Show private lexical permission candidates in the audit CLI while leaving enforcement unknown.
- 6d3bd89: Recheck a declared instruction procedure-shape hypothesis before an explicit improvement run.
- 20e6b29: Include private protected-path metadata candidates in the audit CLI output without claiming enforcement.
- 3921940: Inventory common protected-path candidates privately without reading file contents.
- 2c733d1: Expose realized sampling budget overruns without censoring audit draws.
- 7d9c70c: Recheck selected skill-description collisions before an explicit improvement run.
- bef062b: Recheck an explicitly declared soft instruction-line budget before an improvement run.
- 8e39cbf: Add an opt-in stratified hard-budget audit design.
- e2e0412: Recognize a bounded configuration constraint cue in private hook candidate reports.
- 8e22f26: Keep instruction totals unknown when whitespace budget cuts leave a chain truncated.
- e43df78: Refuse audit strata with zero inclusion probability after rounding.
- ecae663: Detect backticked variable renames with a versioned similarity algorithm while rechecking existing v1 evidence under v1.
- 566c4c1: Reject task graders that pass the unchanged frozen fixture during bank-control audits.
- 8f41a38: Reject out-of-range binary triage values without overflowing validation.
- 8a6d5a9: Add an opt-in binary triage report measuring coverage across all declared roots.
- 2251445: Bound drift input runs and reject over-limit streams without a partial receipt.
- b663813: Include campaign commands in recursive CLI help checks and describe their planned-only actions.
- 9dc92cb: Respect request-reply order when evaluating declared clarification episodes.
- 22fe0d3: Keep cleanup guard exceptions stable when unrelated lines move.
- a2d01ba: Strengthen CLI help regression checks for recommendation and structural repair boundaries.
- 5403d99: Exclude conflicting run identities and affected roots from drift summaries.
- 5269c1e: Use the documented trial limit for unconfigured one-call workflows.
- 67480fb: Keep construction refinement and feedback-tampering regression checks independent of host scheduling delays while retaining separate real-time deadline coverage.
- c316cca: Require live controller role issuance for trajectory grading and protect its action observations.
- c92e504: Keep returned controller usage snapshots independent of live accounting.
- 58d834a: Block exact summary-to-evidence aliases across diagnostic corpus partitions.
- 352f161: Check repeated-workflow prompt similarity by default and report incomplete bank inspection without hiding observed candidates.
- 7a2ba64: Organize documentation by reader purpose and separate workflow and lineage reference material.
- 0c38432: Offer a private descriptive task-mix distance with explicit insufficiency conditions.
- 008ba44: Keep explicit unknown identity placeholders out of precise drift strata and outcome counts.
- cad7db9: Keep hook event coverage unknown when embedded skill or agent hooks are uninspected.
- 5269c1e: Include configuration resolution and preflight in the improvement timeout, including repairs and the first watch attempt.
- 6aa2950: Escape imported labels in benchmark Markdown reports.
- 44d8f80: Explain how to review changed evaluation manifests and mismatched trial partitions.
- 3c00300: Report exact sequence coverage using only the compared positions.
- f05294e: List filename-only feedback sensor candidates in private static audits.
- f0a9ea2: Document and test fixed-interval coverage under independent and shared synthetic root draws.
- 2eaaf70: Reject fixed-comparison recommendations when the in-scope harness gains files during execution.
- 29f8741: Connect fixed-size audit samples to local corpus review assignments.
- f3ba50a: Report conservative fixed-analysis bounds for missing or ungraded paired outcomes while keeping decisions incomplete.
- 952dfd2: Surface cleanup failures in manually driven test fixtures and guard against bare unittest cleanup calls.
- 3b651db: Reject changed evaluation manifests and mismatched trial partitions before refinement feedback.
- ea51f6f: Reject Gitea issue and pull request captures with inconsistent repository IDs.
- f7b9a59: Preserve selected Gitea v2 coverage when parent issue or pull request reads fail.
- 6fc8d93: Keep missing Gitea v2 review listings unavailable rather than inferring endpoint support.
- 58f199a: Recheck selected bank-backed root similarity evidence for controller history and evaluator analysis.
- 5269c1e: Summarize improvement outcomes and explicitly unknown budget measurements.
- 3f6b13e: Keep missing request assertions unknown when controller response output may be incomplete.
- 4412f38: Report conditional instruction ordering in the private static audit.
- f48f2b1: Show whether fixed-plan precision checked root similarity, including an explicit warning for legacy unpinned plans.
- babb188: Exclude lineage links whose supporting cache revisions are stale or ambiguous.
- 7e22ab1: Retain bounded, private live response facts for future interaction evaluation.
- 5269c1e: Report measured improvement time budget while leaving unobserved spending unknown.
- 73ea582: Keep mechanism coverage open when records with the same key disagree on evidence or other record details.
- 5269c1e: Verify configured Codex and Claude first-run controller routes.
- 27e213c: Require native-shaped evidence before reporting cached pull-commit roles.
- 65db681: Limit cooperative native trial environments and record their names and digest; allow explicit full inheritance.
- 5269c1e: Verify ambiguous runtime selection stays noninteractive and blocked.
- 72e6b86: Preserve a verified critical trajectory observation when execution ends before artifact grading.
- da5d82f: Record a bounded kernel-observed worker executable digest when supported, without granting certification authority.
- 9fccfb7: Add a reproducible offline audit walkthrough using a deliberately flawed synthetic fixture.
- eb95f6b: Bound OpenCode backup worker output while it is read.
- 566c4c1: Track frozen oracle reference reuse across bundle changes and fail closed on incomplete coverage.
- 88c2420: Record unvalidated plugin manifest hook-key presence in private version 2 inventory metadata.
- 9235e92: Prepare an explicit private plan and bank similarity evidence without modifying existing inputs.
- eb4bc47: Report conservative incomplete outcome bounds for frozen preservation cohorts without granting promotion authority.
- a846afc: Show bounded action metadata in private controller history reports.
- b2ea08d: Use the selected output directory for analysis and ledger staging when the output path remains stable.
- 5bfcdea: Group private report task observations by recorded status.
- fce8baa: Show paired task observations in private controller history reports.
- 5269c1e: Document supported one-call profiles and platforms.
- 9fb3aee: Prepare the project lock token before creating its lock file so token failures leave no stale lock.
- c7a2424: Exercise journal recovery after abrupt process death at promotion write boundaries and document observed recovery limits.
- 4755560: Test that synthetic proposer authorization, policy, human-approval, foreign-reference and evidence-instruction claims cannot grant promotion or change review-plan authority.
- c77b6f9: Show private protected-path review guidance alongside narrow declared write-tool hook candidates.
- 96fb56f: Reject unknown fields and wrapped reports in public benchmark exports, retaining the documented flat runtime-pair format and caller-declared provenance checks.
- 77767ad: Convert surface, telemetry, trajectory, workflow, hook and source tests to pytest fixtures and parametrized cases. Share synthetic fixture helpers across consumers, preserve cleanup errors, and retain unittest compatibility for standard-library-only runners.
- 65bea7d: Explain read-only filesystem errors without changing application or sandbox permissions.
- e91f9ac: Reject regression-bank revisions that do not bind their own admitted task to the bank and admission catalog.
- 4a23e48: Allow repeated workflows to freeze and recheck private prompt-similarity evidence in stored analyses.
- da5d82f: Bind reviewed-bank reservation evidence to live controlled comparisons without granting promotion authority.
- 6c5f688: Flag local prompt-similarity candidates and apply frozen candidate groups to fixed-plan uncertainty bounds.
- 026a494: Report a conditional anytime envelope across possible completions of a partial sequential prefix.
- 9809285: Offer non-authorizing finite-bank completion ranges for partial sequential prefixes.
- 241fefa: Expose unverified sequential completion ranges for supplied records and preserve the full range for an empty prefix.
- 5269c1e: Share validated configuration resolution across onboarding and one-call routes.
- be33000: Treat task prompts with no lexical shingles as incomplete similarity evidence.
- ded92c2: Reject orphan skill assets hidden by unrelated filename substrings.
- 28841db: Inventory bounded private skill frontmatter hook key fingerprints with unvalidated declarations left unknown.
- 7623127: Record whether each evaluator trial requests an explicit skill preload prompt without inferring activation.
- 98fe8e0: Check used single-line reference-style links in staged skill bundles.
- 1ebb580: Keep the controller's public usage view independent of live accounting.
- c42888c: Reject stale improvement recommendations when the in-scope harness gains files during evaluation.
- 8f02ea0: Reject proposal results when the in-scope harness or project configuration changes during preparation.
- a8dfb77: Connect stratified audit samples to local corpus review assignments.
- 31412ec: Reject stale no-change results when the in-scope harness or governing instruction context changes during evaluation.
- 913de81: Keep trajectory receipt coverage incomplete when predicate fields are unavailable.
- a7370bd: Offer version 2 private trajectory receipts with bounded predicate kind and selected stage labels.
- c488975: Preserve bounded child assertion claims in private trajectory summaries.
- 159cbba: Add a private observe-only triage audit monitoring join.
- a8a42a8: Reject invalid triage values safely and report unrepresentable floating-point costs as unknown.
- c88e025: Offer versioned triage decision coverage that includes unlabeled roots.
- f9e3dd3: Keep declared hook event coverage unknown when an unsupported event has no handlers.
- 816cb18: Keep hook event coverage unknown when enabled plugin content has not been inspected.
- 5297c90: Keep unverified critical-regression claims incomplete and handle graded, executable-grader-labeled regressions consistently across record order in fixed paired analysis.
- 3d2ae6c: Preserve version 1 harness inventory output while binding newer audit evidence in version 2.

## 0.152.0

### Minor Changes

- dc160df: Extend the CLI and skill receipt parity contract to applied outcomes: the per-application deployment ID and rollback command may differ, and tests cover applied parity, dirty-change preservation through rollback and a blocked skill run that leaves the target unchanged.

## 0.151.0

### Minor Changes

- 6b3b537: Add an opt-in frozen resource contract and paired capped cost and latency decision that never accepts a saving while quality is unverified, keeps timeouts and unknown measurements visible, and stays recommendation-only.

## 0.150.0

### Minor Changes

- 0cdd7fc: Add skill metadata, disclosure and routing-collision checks to the static audit

## 0.149.4

### Patch Changes

- 216d00a: Move the memory capture and provenance, native compatibility, OKF, permission composition, plugin and workflow inventory, recheck, store, temporal, test results, transport and starter bank tests to pytest style. Each former `subTest` case is now its own parametrized test, `setUp` and temporary directories become fixtures, and no assertion site is dropped. `SourceStoreTests` keeps its `setUp()` and `addCleanup` interface for the lineage and chunked read fixtures, `Response` in the transport module is unchanged, the recheck tests run through `tests/support/github_case.py`, and the permission composition cases release their hand-driven `unittest.TestCase` hosts with `run_cleanups` and `run_class_cleanups`.

## 0.149.3

### Patch Changes

- 4fd4d7f: Move the Claude and Codex native source, Claude instruction, rule and Claude-Mem, Cognee, command, commit-join and config inventory tests to pytest fixtures, plain asserts and parametrized cases, keeping every assertion. `NativeSourceTests` keeps the interface the other source tests build on, and the Claude root and commit-join fixtures release their hand-driven TestCase helpers with `run_cleanups`, so a failed nested cleanup is no longer dropped. The fixture cleanup probe covers both new fixtures.

## 0.149.2

### Patch Changes

- 1ac7060: Move the source context, contract, controller, dispatch, Git, Gitea, GitHub (core, commits, details, threads, live), execution and frontmatter tests to pytest style. Each former `subTest` case is now its own parametrized test, `setUp` and temporary directories become fixtures, and the helpers that other test modules import keep their interfaces: `GitHubFixture` stays a plain mixin for the unittest-style GitHub modules, with `GitHubCase` as its pytest host, `SourceDispatchTests` keeps its `setUp()` and `doCleanups()` for the normalized export tests, and `metadata_for` and `Response` are unchanged. The Taskfile selectors follow the renamed `TestControllerSource` and `TestGitHubLive` classes.

## 0.149.1

### Patch Changes

- bb70abf: Move the GitHub window, GitLab, graph, content grouping, harness binding, Hindsight, hooks inventory, instruction, lineage, MCP and Mem0 source tests to pytest style. Each former `subTest` case is now its own parametrized test, `setUp` and temporary directories become fixtures, and no assertion site is dropped. `GitHubFixture` users in the window and graph modules run through the new `tests/support/github_case.py` host, `MCPReaderTests` keeps its `setUp()` and `doCleanups()` interface for the memory capture tests, `graph_for`, `SyntheticHarnessRepository` and `git_available` are unchanged, and the lineage and grouping fixtures that drive a `unittest.TestCase` by hand release it with `run_cleanups`.

## 0.149.0

### Minor Changes

- 98afa39: Add an opt-in interaction contract for grading the evaluated task agent's clarification, deliberate stop and no-change behavior (#123). `evaluation.interaction_contract` validates a versioned contract with a stored digest and grades observed evidence into separate three-valued `interaction_requirement_satisfied`, `whole_task_completed` and `state_preserved` results, so asking at a declared boundary no longer implies a finished task. Crashes, timeouts, missing transcripts or receipts and unsupported continuation stay unknown or failed, never a stop. Existing artifact-only tasks and graders are unchanged; the evaluator is not yet wired to the contract.

## 0.148.0

### Minor Changes

- 9a76891: Write a `hook-review.json` packet beside hook canary results: the semantic hook-entry and script delta, capability receipt, canary disposition and whole-run wall time, with task effect, the instruction-only comparison and post-apply verification stated as not performed (#80).

## 0.147.0

### Minor Changes

- c9db95c: Let audit sampling plans commit the seed by hash before the window closes. Receipts record the commitment and revealed seed, refuse a mismatched seed or a late commitment, and drop `seed_selection_unverified` only when the check passes.

## 0.146.1

### Patch Changes

- e657c9c: Move the plugin recommendation, precision solver, preservation, private and promotion journal, project sync, published release, refinement input and regression admission, control and proposal tests to pytest fixtures, plain asserts and parametrized cases, keeping every assertion. `PreservationCohortTests`, `PreservationHistoryTests`, `PreservationReceiptTests`, `RegressionAdmissionTests`, `RegressionControlTests` and `make_draft` keep the interface other test modules build on. `test_runners.py::test_unittest_bound_file_runs_fully_without_pytest` already runs the standard-library discovery of the unittest-bound files with pytest blocked, so a helper module they import that takes pytest directly fails the suite.

## 0.146.0

### Minor Changes

- e96a062: Let an `instruction_plan` justify each changed instruction or rule file with an `operations` list (#79). Each entry cites a mechanism id and evidence labels, states the expected change, the required behaviours it preserves and a falsifier, and declares one of `add`, `update`, `remove` or `consolidate`. Once a plan lists any entry, `instruction_validation.validate` blocks an unjustified change, an entry for an unchanged path, and a declared decision that differs from the computed one, and the report gains a `decisions` list that keeps text only as hashes. A plan without `operations` is unchanged. A justification is a declaration and grants no adoption authority.

## 0.145.1

### Patch Changes

- 0ad3374: Replace the normal (Wald) intervals in `diagnostics.audit_sampling` with intervals that keep nominal coverage when only a few incidents are sampled (#386). Each stratum now gets the exact binomial-index analogue of the Clopper-Pearson interval under the Poisson design. A stratum with no sampled case adds its zero-event upper bound instead of a certain zero, and the limits combine across strata by MOVER. Triage recall applies MOVER to the log ratio of missed to alerted incidents. Counts and rates are marked `interval: exact_binomial_mover` and recall `interval: exact_binomial_mover_log_ratio`. The zero-event bounds for estimates with no sampled case are unchanged. Receipts now list the `multi_stratum_intervals_approximate` limitation in place of `normal_approximation_intervals`. Exact-enumeration tests with 1 to 5 sampled incidents keep two-sided coverage within tolerance of 95%, where the Wald interval failed 11 of the 16 cases.

## 0.145.0

### Minor Changes

- e7965f9: Validate staged project skill bundles as a whole before evaluation and adoption, so a missing entry point, malformed metadata, dangling references and orphan assets block a skill create, update or removal (#78).

## 0.144.1

### Patch Changes

- 824df68: Move the sandbox kernel, resource, storage and working-state, scope receipt encoding, search history, sequential and sequential kernel, skill invocation, agent inventory and Claude source tests to pytest style. Each former `subTest` case is now its own parametrized test, `setUp` and temporary directories become fixtures, and the helpers that other test modules import keep their interfaces: `KernelFixture` and the unbound kernel test methods stay callable from the unittest-bound bank qualification tests and the permission composition tests, and `ClaudeSourceTests` keeps its `setUp()` and `doCleanups()`.

## 0.144.0

### Minor Changes

- 0cf5ba9: The static harness audit now follows the Codex project-doc budget over the `AGENTS.md` directory chain for `task_cwd`, as the pinned `openai/codex@d786ff91` source loads it: the file that crosses the default 32 KiB `project_doc_max_bytes` budget is `nonconformant` (`instruction_chain_truncated_at_default_project_doc_bytes`) and each later non-blank file is `nonconformant` (`instruction_chain_skipped_at_default_project_doc_bytes`). Findings gain `measures` with the budget, loaded and dropped byte counts, so the finding names the crossing file and what Codex drops. A chain that fits adds nothing, and the chain is not judged when the checkout root has no `.git`, the project is declared untrusted, `task_cwd` is unknown, or a candidate is refused or unparsed. A configured budget is never read. The Codex instruction parser records `codex_leading_blank_bytes` to follow Rust's blank-file rule.

## 0.143.1

### Patch Changes

- b1e245d: Move the regression replay and review, release install, repeated workflow, review authority, run-queue gate, runtime boundary and sandbox cgroup, construction, deadline, grader, image and inference tests to pytest fixtures, plain asserts and parametrized cases, keeping every assertion. `RegressionReplayTests`, `ReviewFixture`, `PublicFixture`, `ControlledWorkflowTests`, `SandboxedGraderTests`, `ControllerInferenceTests` and `ResponsesEvaluationTests` keep the interface other test modules build on, and now release their nested helpers with `run_cleanups`, so a failed nested cleanup is no longer dropped. `test_sandbox_inference` takes pytest from `tests/support/optional_pytest.py` because the unittest-run derivation falsifier imports it, and the fixture cleanup probe covers the new fixtures, including the repeated-workflow kernel scopes.

## 0.143.0

### Minor Changes

- 1cc4ac7: Add `agent_harness_lab.diagnostics.drift`, a library API for frozen `drift-plan-v1` monitoring plans, count-only `drift-reference-v1` references and `drift-receipt-v1` receipts for one non-overlapping fixed window. The receipt keeps capture, task-mix and verified-performance drift in separate channels. The capture channel alerts on schema mismatches, conflicting duplicates, cursor gaps or collisions, untimed runs and coverage below the frozen rule. Runs with partial or unknown capture, missing fields, or unknown task root or harness identity never enter the task-mix or performance counts. Retries of one task root count once, requested model aliases cannot become strata, and missing, unverified or mixed outcomes stay unknown. The task-mix and performance detectors are not implemented yet, so those channels report descriptive exposure as `insufficient_evidence`, and every receipt states `regression_label: not_established` with no rollback or promotion authority.

## 0.142.0

### Minor Changes

- 09fb934: Add `sources.knowledge_links`, an offline reader of the links between the Markdown pages of one selected knowledge bundle (OKF export, LLM wiki, Markdown knowledge graph or ADR directory). It turns `[[wikilinks]]`, embeds, inline links and images, reference definitions and autolinks into provenance-preserving edges with an explicit `resolved`, `missing`, `ambiguous`, `outside_bundle`, `remote`, `unknown` or `invalid` status, skips code, never opens files or fetches remote links, and refuses oversized bundles instead of truncating them. Its counts-only `knowledge-links-v1` summary is never publishable.

## 0.141.3

### Patch Changes

- b235f6e: Move the certification access, chunked read, Claude and Codex capture, CLI help, surface smoke, closed project input, cohort requalification, collection progress, commit message and configured execution tests to pytest style. Each former `subTest` case is now its own parametrized test, `setUp` and temporary directories become fixtures, and the capture builders that other test modules import keep their `setUp()` and `doCleanups()` interface.

## 0.141.2

### Patch Changes

- 57b9d2e: Move the offline evaluation, onboarding, one-call, optimization history, oracle and permission audit tests to pytest fixtures, plain asserts and parametrized cases, keeping every assertion. `OfflineFixture`, `OracleHistoryTests`, `OracleBankAuthorityTests`, `OraclePlanTests`, `OneCallTests` and the oracle behavior `fixture` keep the interface other test modules build on. Modules that the standard-library unittest runners import for helpers now take pytest from `tests/support/optional_pytest.py`, which stands in with pass-through decorators when pytest is not installed.

## 0.141.1

### Patch Changes

- 59732d1: Move the hook contract, installed layout, instruction context and improvement, contained-read integrity, interchange, isolated grading, skill linking, mechanism proposal, monitoring, native peak memory and normalized export tests to pytest fixtures, plain asserts and parametrized cases. Every assertion and every Taskfile selection stays in place. The `hooks` and `no_execution` helpers, `fingerprint` and `FixedNow` keep the interfaces that other test modules import.

## 0.141.0

### Minor Changes

- 57bbeeb: Add `diagnostics.audit_sampling`, an offline probability-sampled audit of alerted and non-alerted episodes (#75). A frozen frame records capture status and exclusions for one window and scope. A plan bound to its hash declares a mandatory random route plus optional alerted, severe, uncertain and out-of-distribution oversampling, and refuses zero or invalid rates and budget overruns. `draw` replays a seeded Poisson sample in which overlapping routes combine into one draw with a known inclusion probability, and seals a receipt before labels exist. `estimate` reports Horvitz-Thompson prevalence, missed-incident and triage-recall estimates with Poisson-design normal intervals. An estimate with no sampled case reports a zero-event upper bound instead of a zero-width interval. Pending or nonresponding reviews make the result inconclusive, with the outer 95% limits of the all-negative and all-incident fills instead of counting as negatives. Receipts record that the seed is chosen after the frame and is not verified. Alert-only counts and the unweighted review composition are kept separate and marked as non-population figures. Receipts and reports carry unit hashes only and are refused for public export.

## 0.140.0

### Minor Changes

- 84461b0: Add an opt-in `actions` detail to GitHub selection version 7 that reads workflow runs for each selected PR head and the jobs and steps of every attempt. Failed jobs and steps stay bound to the exact head and attempt, reruns stay correlated attempts rather than independent trials, unknown states reject the capture, and logs, artifacts and annotations are reported as not captured.

## 0.139.0

### Minor Changes

- e5e20d0: Add the model-free static harness audit library `agent_harness_lab.audit`. `audit.run(checkout, runtime)` reads the harness surface inventory at the pinned runtime version and reports one row per dimension (instructions, rules, hooks, workflows, agents, commands, plugins, config) as `conformant`, `nonconformant` or `unknown`, plus findings with fixed remediation labels ordered cheapest first. It reads only the facts the inventory parsers already recorded: nothing is executed, written, sent over the network or given to a model. Unread (`private_excluded`), unparsed, refused and unchecked surfaces stay `unknown` and never count as a pass, and a blocked or unsupported inventory leaves every dimension `unknown`. Plugin manifests and marketplaces are checked only when a selected plugin checkout's inventory is passed to `audit.audit()`. Each row is labelled a convention check, not performance evidence, and no aggregate score is emitted.

## 0.138.0

### Minor Changes

- d6db232: Add `evaluation.side_by_side.build`, a read-only private review model for one baseline/candidate experiment (`ahl-side-by-side-experiment-v1`). It pairs records by task, root, family and repeat, never by list position, and gives each pair an `improved`, `regressed`, `unchanged`, `unknown` or `not_comparable` verdict. Missing and ungraded arms are unknown, not failures. Duplicate, unplanned, invalid and hash- or runtime-mismatched pairs stay visible but are not comparable. Timelines align events only by a shared stable ID or an explicit mapping, and keep only allowlisted metadata, so message bodies, tool arguments and results and reasoning never reach the report. Unknown capture and usage stay unknown, a supplied analysis is copied verbatim and never recomputed, and the report is refused for public export. There is no CLI or HTML viewer yet.

## 0.137.1

### Patch Changes

- 18a97f3: Make the hook-validation tests for lingering processes independent of runner speed. The background child now holds a FIFO gate open and blocks on it until the test releases it, so the test checks at the post-run snapshot and after the trial whether any process the run left behind is still alive, instead of racing a one-second sleep. The containment behaviour is unchanged.

## 0.137.0

### Minor Changes

- 7dc810f: Add `LexicalIndex.contrast` and `ahl incident-search --hypothesis LABEL [--control-label LABEL]`, an `incident-contrast-v1` report that groups lexical matches into supporting, contradicting, control and unresolved (`unknown`/`ambiguous`) cases, each bounded separately by `--top-k` so close supporting matches cannot hide counterexamples. Results carry root hashes instead of raw root IDs, labels, evidence references and provenance, and withhold summaries from private records. The report counts distinct and shared roots and unmatched hypothesis-labeled incidents, and always states `diagnosis: hypothesis_only`, `causal_claim: false` and a non-estimable prevalence.

## 0.136.0

### Minor Changes

- 04959df: Add `evaluation.adapters.AdapterSpec`, an opt-in declarative capability contract for one presentation adapter (`ahl.adapter-spec.v1`), and `review_candidate_spec`, a non-executing check of an untrusted candidate against the controller-owned spec and project authorization. A spec that declares side effects or any filesystem, network, credential or nested-tool grant is rejected, and so are limits above the fixed ceilings and unknown or missing fields. A candidate that adds a tool, transform or grant, raises a limit, renames the adapter, or changes its contracts or runtime is rejected. New implementation bytes need a `previous_digest` equal to the authorized spec's `content_digest` (the implementation SHA-256, not the spec digest), and unchanged bytes must keep the authorized `previous_digest`. The review never runs adapter code or promotes, and it reports runtime integration as unsupported. Nothing calls these APIs by default.

## 0.135.1

### Patch Changes

- c7e0fc5: Move the test suite from `tools/tests` into a top-level `tests/` package with `tests/unit/<area>/`, `tests/integration/<area>/` and `tests/support/`, using absolute imports between test modules. pytest uses `--import-mode=append` so installed wheel and archive runs still load the installed package, and a guard test fails on any test module outside `tests/` (the evaluator's own checks stay beside its scripts). Collected tests and every Taskfile selection are unchanged; wheel and release archive contents are unchanged.

## 0.135.0

### Minor Changes

- 9563b0b: `sources.ref_movement.ref_movement` classifies how one ref, or HEAD, moved between two checkpoints of a `local_git/git/v2` journal. The statuses are `unchanged`, `fast_forward`, `non_fast_forward`, `appeared`, `disappeared`, `unobserved` and `unknown`. Ancestry comes only from captured native parent edges, and a history ceiling or shallow boundary keeps a rewrite `unknown`. A non-fast-forward move keeps its cause `unknown`: a force-push, reset, rebase or squash look the same. The function reads only the journal and runs no Git.

## 0.134.1

### Patch Changes

- 24cf262: Move the diagnostic corpus benchmark, docs, duplicate hook, effective agents, evaluation foundation, evidence, experiment, grader budget, control and correction, harness change plan, harness inventory and history report tests to pytest fixtures, plain asserts and parametrized cases, keeping every assertion and selected test. `TaskBankTests`, `GraderCorrectionTests` and `InstructionFixture` keep the `setUp` and `doCleanups` interface that other test modules build on.

## 0.134.0

### Minor Changes

- 80c4f89: Add `diagnostics.coverage.report` and `render_markdown`, a private `mechanism-coverage-v1` JSON and escaped Markdown report over mechanism records or `run_queue` output. Each mechanism keeps its responsible component, claim level (`observed_pattern`, `supported_diagnosis`, `development_recommendation`, `validated_improvement`), disposition and coverage state separate. A record without captured evidence is `unknown_capture` whatever its disposition, a disposition the record cannot support stays open, and complete coverage requires every mechanism resolved plus an explicit complete-capture declaration. Routes and claim levels are reported as hypotheses, not causal proof.

## 0.133.1

### Patch Changes

- 1362723: Convert fourteen configuration, construction, controller, correction, derivation and diagnosis test modules from `unittest.TestCase` to pytest fixtures, plain `assert` and `pytest.mark.parametrize`, without dropping a case or an assertion. `task test:evaluation:history-identity` now selects `-k TestHistoryIdentity` and `task test:configuration:responses:kernel` selects `TestConfiguredResponsesKernel`, so both still run the same tests. The shared harnesses that other test modules build directly keep their `setUp` and `doCleanups` methods.

## 0.133.0

### Minor Changes

- df8eef9: Add `evaluation.skills.paired_transfer`, a paired skill-present versus skill-absent report over matched task roots under a predeclared target/control policy with a declared `max_control_loss`. A skill that helps its target tasks but loses more than the tolerance on an unrelated control is rejected as negative transfer, and a critical regression also rejects it. An observed activation in the absent arm, or missing matched target or control roots, is inconclusive. Preload stays a diagnostic condition, and the main contrast never filters on activation. Activation counts only as an `activated: true` record with evidence. An injected hash, `activated: false` or a bare label stays unknown. Unverified and unidentified records are excluded and counted. The report never certifies or promotes.

## 0.132.3

### Patch Changes

- 989fe5b: Move the adversarial bank, agent definition, analysis, bank correction, export, inspection, requalification, behaviour and benchmark tests to pytest fixtures, plain asserts and parametrized cases, keeping every assertion and selected test.

## 0.132.2

### Patch Changes

- 6ea80a9: Give offline evaluation roles and sandboxed graders an empty session keyring. `SandboxBackend.command()` now launches bubblewrap through the same host-side keyring trampoline as hook trials, which joins a new empty session keyring and installs a seccomp filter that fails `add_key`, `request_key` and `keyctl`. A candidate or grader therefore cannot search, describe or read the validator's keys, even by serial number; bubblewrap's namespaces do not isolate keyrings. `/proc/keys` still lists the type and description of keys the validator's uid may view. A kernel without key support proceeds, and any other failure stops before bubblewrap starts. `ahl sandbox-check` then reports `keyring_isolation_unavailable`. Permission-audit runs launch their observer through the trampoline, so the traced exec chain is unchanged. `SandboxBackend.bubblewrap_command()` returns the bubblewrap argv alone for nested launches.

## 0.132.1

### Patch Changes

- 7171d8d: Stop the sandbox process bound from failing every run on a busy host. Without a cgroup root, `SandboxBackend.run()` set `RLIMIT_NPROC` on the launcher, which counts every process of the invoking real UID, so bubblewrap's first clone failed with `EAGAIN` once the user ran more than 32 tasks. The cap is now set on the sandbox's first process inside its own user namespace (Linux 5.14+), while bubblewrap holds it on `--block-fd`, so it counts only the sandbox's processes. When no sandbox-scoped bound is available, the run continues and its attestation reports `process_bound` as not enforced, and `ahl sandbox-check` lists `no sandbox process bound`. A probe whose clone fails with `EAGAIN` now reports `process_limit_exhausted` instead of `namespace_probe_failed`.

## 0.132.0

### Minor Changes

- 2509dfd: Add the opt-in `sources.okf.ReconcilingOKFExport` reader. Through `Registry.ingest` it compares an ai-memory OKF bundle with the journal's committed page revisions. It records one unconfirmed `tombstone` for each page path missing from the bundle, and a new revision bound to the latest one when a page reappears or reverts to bytes committed earlier, so each path's latest journal revision matches the bundle. Absence is reported as not deletion evidence, renames are not inferred, the checkpoint stays the bundle fingerprint, and `ahl sources import` keeps the default reader.

## 0.131.1

### Patch Changes

- 940e98c: Run the Taskfile test tasks with pytest instead of `python -m unittest`, keeping each task's selected tests. Repeated unittest `-k` filters become one `or` expression, and filter variables such as `KERNEL_TEST_FILTER` now take a pytest `-k` expression. `test:evaluation:bank-qualification` stays on unittest because its wheel variant has no pytest, and the pytest-patterns adaptation lists the files that standard-library-only runners still execute.

## 0.131.0

### Minor Changes

- 42e6c82: Add a seeded Monte Carlo power simulation for frozen fixed-final experiment plans. `ahl experiment --spec PLAN --power-model MODEL [--simulations N] [--simulation-seed S]` draws synthetic trial records from a declared outcome model (baseline success, common or per-family effect, root spread, repeat correlation, critical-regression probability) and judges each set with the unchanged `statistics.analyze` engine. The private `fixed-root-power-simulation-v1` report gives disposition counts, rates and Wilson 95% Monte Carlo intervals, labels the supported rate as power or false-recommendation rate, and keeps roots and repeats separate. No worker or model runs, cohort-pinned and sequential plans are refused, and automatic promotion stays false.

## 0.130.0

### Minor Changes

- 8b376a5: Run `ahl evaluate hooks` trials under the existing bubblewrap backend by default (`--sandbox bubblewrap`). The run has no network and a cleared environment, and it sees only the system directories, the runtime executable and the trial root, read-only at its own path. Only the trial `home`, `tmp`, `codex-home` and `.ahl-canary` folders are writable, and the user's home and credential stores are not mounted. When bubblewrap or user namespaces are unavailable, or the backend's namespace probe fails, the run is `inconclusive` (`sandbox_unavailable`) and nothing executes; there is no unsandboxed fallback. `host_isolation` is `bubblewrap` only when the shipped backend wrapped the run, `none` for `--sandbox none` or a run that never executed, and `unverified` for any other sandbox object. Results add a `sandbox` block. `SandboxBackend.hook_trial_command` builds the hook trial's mounts.

## 0.129.0

### Minor Changes

- cb7875d: `ahl analyze --source-root` now accepts a bounded Claude `projects` directory with the pinned `claude-jsonl-2.1.316` profile. It reads only the project's mapped directories through the source-journal root reader, selects an exact session UUID with `--session`, and reports reached limits, unattributable or unrecognized files and missing directories as coverage caveats.

## 0.128.0

### Minor Changes

- 758f473: Report the owned Git harness snapshot binding inside the live controller source receipt. `SourceSink` accepts an optional `harness_capture`; each successful `finish` then adds `live_verification.harness_snapshot_binding`, the same receipt `harness_binding.bind` returns for that attempt, next to the verified session, attempt and grader revision. Unowned captures report `unknown`, incomplete attempts report no binding, and nothing new is journaled, imported or added as an identity field.

## 0.127.1

### Patch Changes

- 54c8e84: Add the pinned ruff linter (`ruff==0.17.2`, in a new `lint` extra that `task setup:test` installs). `pyproject.toml` selects pyflakes and syntax-error rules (`F`, `E9`) for Python 3.11 and excludes frozen fixtures. `task lint` and `task lint:fix` run it, CI lints before the suite, and the lefthook pre-commit hook lints staged Python files. The tree is clean: unused imports and variables are removed and deliberate re-exports are marked.

## 0.127.0

### Minor Changes

- 0c03bdb: Add an offline PR commit-role join. It names the roles an exact local commit holds in one GitHub pull request (head, base, merge result, test merge or listed member) from native PR edges in the latest checkpoint. A head recorded only in an earlier checkpoint is reported as superseded, so a stale head never counts as current. A missing edge stays unknown and never becomes unlinked, and the private report carries only candidate receipts with no outcome or authority.

## 0.126.0

### Minor Changes

- dec8127: Add opt-in GitHub conditional requests: `GitHubAPI(..., conditional=True)` sends `If-None-Match` with validators held in memory from a prior successful `200`, and replays a `304 Not Modified` as the previously validated response charged its original bytes, so an unchanged recapture yields the same snapshot and never advances the checkpoint. Unsolicited or mismatched 304s reject the capture, failed captures keep the last successful validators, and validators never reach the journal or summary. The default sends no validators.

## 0.125.1

### Patch Changes

- 964eeba: Add the MIT-licensed pytest-patterns skill under `.agents/skills/` with a repository adaptation note, as the reference for the test-suite refactor.

## 0.125.0

### Minor Changes

- f17075e: Document that Codex hook changes stay recommendation-only. At the pinned `openai/codex@d786ff91` source, Codex runs project hook commands in the session working directory and sets no project-root variable, so the hook contract keeps blocking every Codex unit. The source contracts cite the files and lines, and new synthetic tests pin that no Codex command form gets the project-root anchor.

## 0.124.1

### Patch Changes

- 2f0cac3: Bind the governing scope of scoped instruction and rule rewrites to the evaluated tasks instead of the proposer's plan alone (#339). After the evaluation manifest is validated and before baseline or candidate trials run, `instruction_validation.bind` checks the plan's `task_cwd` against the task `cwd` values, where a task without one runs at the root, and checks that each task path lies below that `task_cwd` and an evaluated cwd. A task `cwd` counts only under the controlled Responses executor, which runs the trial there; elsewhere a manifest that declares one is refused. Task paths stay proposer-declared (`scope.task_paths_source`), so the bound scope drops the nested instructions and path rules only they make governing (`scope.task_path_scope`). A plan that declares an unrelated `task_cwd` or task path to reach a sibling `CLAUDE.md`, `AGENTS.md` or rule is refused as `instruction_validation_blocked` (`task_cwd_not_evaluated`, `task_path_not_evaluated`, `task_path_scope_unbound`). The report records `scope.source: "evaluation_manifest_task_cwds"` and `scope.task_cwds`, `check_scope` enforces the bound scope, and `current` reports `scope_unbound` for a scope that was never bound, so no recommendation rests on a proposer-declared scope.

## 0.124.0

### Minor Changes

- cf9a16c: Add the generic harness surface matrix. `surfaces.matrix()` gives each of the eleven surfaces (instructions, path rules, hooks, workflows, agents, commands and skills, plugins, MCP, output styles, memory and settings) its Claude Code and Codex writability and the route a change gets (`adopt`, `contract`, `recommendation` or `excluded`), computed from the classifier rows for one synthetic path per runtime. `ahl sources inventory` prints the declared runtime's column as path-free `surfaces` rows. The change-plan deferred lists of `ahl improve .` and `ahl improve . --repair-contracts` now come from `surfaces.deferred()`: the behavioral candidate defers every surface no runtime routes to adopt, and the contract path defers every surface but skill headers (and `hooks` until every hook contract passes). Memory is excluded, never deferred. The README, a new harness-self-improvement surfaces reference and a new harness-evaluation trial isolation reference document the matrix, the version pins and the unverified items.

## 0.123.0

### Minor Changes

- 938a60e: Add `workflow_contract_v1` to `agent_harness_lab/hooks_contract.py`, which stages Claude Code workflow scripts (`.claude/workflows/*.js`) under the same executable contract as hooks. `stage_workflows` builds journal snapshots without writing the project. The static workflow lint is a hard gate: a candidate whose `meta` is not the first literal, that uses `import(`, `Date.now()`, `Math.random()`, a no-argument `new Date()` or a static `import`, or whose lint is inconclusive is blocked before any run, and so is a removed, group- or world-writable or nested-repository script. A passed unit is admitted by `Journal.prepare(..., hook_contract=...)` like a hook unit and resolves its change-plan operations, but never lifts the deferred `hooks` surface. Analysis never runs a workflow. `trial_request` only describes a native run inside an isolated trial workspace outside the project, with `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` capped at 2 and an args fixture of at most 2048 bytes, and the caller runs it. `outcome` returns `recommendation_only` with a patch when there are no runs, and `inconclusive` when the runs are malformed. It returns `improved` only for graded baseline and candidate runs of such requests. Codex has no workflow loader, and every workflow document records that.

## 0.122.0

### Minor Changes

- e0acd48: Add `ahl evaluate hooks` (`hook_validation_v1`, `agent_harness_lab/hooks_validation.py`). It runs one staged hook unit beside a harmless nonce canary, and only inside a new private trial workspace outside every Git checkout and input. The environment carries no credentials, and `HOME`, `TMPDIR` and `CODEX_HOME` all point inside the trial. The host is not isolated: hooks run as the invoking user and can still reach the host filesystem and network. The unit must pass `hook_contract_v1` again, or nothing executes. Canary evidence is reported as observed, skipped or inconclusive, separately for native and fake runtimes. A real runtime runs only with `--native-canary`. Codex stays inconclusive while `--dangerously-bypass-hook-trust` is an unverified capability row. Malformed events and results, missing executables, nonzero status, timeouts, cancellation, reentrancy, duplicate events and out-of-scope writes (found by a trial diff) are bounded failure classes. Disabled, unmatched and unsupported handlers are never effective. A hook that hides errors, blocks required work or changes permissions cannot qualify as an improvement.

## 0.121.3

### Patch Changes

- 7dd5ef4: Correct the 0.113.0 and 0.114.2 changelog entries to the shipped config inventory: `.mcp.json` and output styles are `config` nodes, `.claude/settings.json` and `.codex/config.toml` keep the `hook` kind and are read by the config parser through multi-surface dispatch, and `.claude/settings.local.json` is presence-only and never read.

## 0.121.2

### Patch Changes

- 2385d7c: Route Claude commands and `SKILL.md` files whose content holds shell pre-execution syntax (`` !`command` `` or a ` ```! ` block) to the hook contract instead of adopting them. Variants that the pinned Claude Code docs do not confirm, and inline forms inside plain fenced code blocks, fail closed.

## 0.121.1

### Patch Changes

- 2a12998: New tests pin how the Codex instruction byte budget treats directories left `unknown` by a declared `project_root_markers` entry that the checkout does not contain. In such a directory the first existing candidate is selected. An empty `AGENTS.override.md` adds no bytes to `project_doc_max_bytes` and still keeps a large `AGENTS.md` beside it from being counted, so the `task_cwd` file fits a budget sized for itself alone. A non-empty selection is counted in place of the large file, and that includes one made only of the separators U+001C to U+001F. The source contract now spells out this rule. Behaviour is unchanged.

## 0.121.0

### Minor Changes

- 1a881cb: Add `hook_contract_v1` (`agent_harness_lab/hooks_contract.py`), a static contract for one hook deployment unit: the `hooks` key of `.claude/settings.json`, `.codex/hooks.json` or `.codex/config.toml` plus the checkout scripts its handlers reference. `promotion.journal.key_edit` rewrites only the `hooks` key of JSON or TOML, so `permissions`, `env` and every other key keep their bytes through create, update and rollback. The contract blocks unknown events, matchers that do not compile (Python `re` as an unverified proxy), non-command handlers, missing or symlinked scripts and unset timeouts, and marks network or credential references recommendation-only. It never writes `settings.local.json`, managed layers or Codex trust state, and Codex units list `requires_user_trust_review`. The journal admits a unit only when a caller passes a contract that it re-evaluates as passed. A partial config-and-script write resumes or recovers as one deployment, and a concurrent edit is a conflict that keeps the user's work. Change plans resolve `contract_required` operations only through a passed contract, and the one-call path drops `hooks` from its deferred surfaces only then. Nothing is executed. Skill frontmatter that registers hooks now needs the contract, as agent frontmatter already did.

## 0.120.0

### Minor Changes

- 047c973: Staged Claude `.claude/agents` and `.claude/commands` files and Codex `.codex/agents` role files are now validated before evaluation by the new `sources/agents_validation.py`, which reuses the agent and command inventory parsers. A create or update that the runtime would skip, reject or partly ignore (no leading fence, a leading blank line, a missing required field, bad YAML or TOML, an unknown or ignored field, a duplicate name) or that declares hooks is `blocked`. A change to a permission field such as `tools`, `permissionMode`, `mcpServers`, command `allowed-tools` in any YAML form, or any Codex role field outside an allowlist of identity, instruction and model-tuning fields is `recommendation_only`. A Codex role update that changes only `sandbox_mode` or another field a role cannot override is flagged `no_override_effect`, and a command shadowed by a skill is flagged `shadowed_by_skill` with a `migrate_to_skill` recommendation. Both are `no_effect` and never count as an improvement, unless the candidate also changes something effective. Removals stay adoptable. `ahl improve` stops such a candidate before evaluation with status `blocked`, `inconclusive` or `no_change`, and `workflow.adopt` refuses it.

## 0.119.0

### Minor Changes

- 9108b07: Add `agent_harness_lab/sources/instruction_validation.py`, which validates scoped instruction and path-rule candidates. It runs the instruction and rule resolvers again on a baseline closure and a candidate closure, each made of the project's in-checkout instruction files with the staged copy laid over them. A candidate that changes a nested instruction or any rule file must declare an `instruction_plan`. The candidate is blocked when it edits outside the governing scope, edits a rule that matches no declared task path, adds an import cycle or missing import, changes shadowing without declaring it, drops a declared task path from a rule's `paths`, moves or removes a required text from the active context, or starts from a stale baseline. `ahl improve` writes the validation report and blocks before evaluation. `one_call.check_scope` now rejects a changed instruction or rule file outside the validated scope, and without one it rejects any nested file the frozen baseline does not hold. Active-context bytes are a secondary measurement. A reduction made by moving text out of the active context earns no credit, and length never selects a winner. Removing active guidance is `recommended` only when regression controls pass in trial workspaces that staged each version faithfully, and it still needs certification. The validation runs again before a recommendation is recorded.

## 0.118.5

### Patch Changes

- 80dfa8f: A frontmatter fence line that ends in spaces or tabs, such as `--- `, is now `unsupported_yaml` in every mode of the shared reader, not only in `texts()`. An agent definition whose opening fence is `--- ` was read as having no fence and labelled `skipped_silently`; it now stays `unknown` with reason `frontmatter_unsupported_yaml`, because whether the runtime trims the fence is not verified. Commands, skills, prompts and path rules keep their existing `frontmatter_unsupported` verdict for such a file. The docs now match the code in three more places. `docs/telemetry.md` no longer says every MCP call gains `loading_signal`: plugin MCP names (`mcp__plugin_...`) and names without exactly two `__` parts gain none. The Codex instruction docs say that a candidate that exists but is not a single-link regular file fails the capture closed, where Codex skips a non-file candidate. Multi-surface dispatch covers only the files in `harness_inventory.COMBINED`: `.claude/settings.json`, `.claude/settings.local.json` (private and never read) and `.codex/config.toml`.

## 0.118.4

### Patch Changes

- e84d947: Config recommendations now refuse with `ValueError` a forged inventory whose rows are each valid but that no capture writes together, such as two same-name server rows without their scope precedence or a settings digest list replaced by a scalar, where they previously crashed with `KeyError` or `TypeError`. A user-layer output style, whose precedence over a same-name project style is not recorded, is now `unverified` and carries `shadowed: null`. `not_inventoried` now lists managed MCP servers, managed settings policies and Codex managed configuration. Plugin bundle `status` returns `dirty_checkout` when a touched path became a symlink, directory, hardlink or special file, and refuses a malformed manifest with `ValueError`. A checkout whose worktree walk visits more than 10000 entries gets the `checkout_bound_exceeded` receipt instead of an error; a worktree deeper than the harness inventory's 32 levels is refused earlier as `inventory_blocked`.

## 0.118.3

### Patch Changes

- a8533ed: Vendored and generated trees are no longer adoptable harness surfaces. A path below `node_modules`, `.venv`, `venv`, `vendor`, `bower_components`, `jspm_packages`, `site-packages`, `__pypackages__`, `.tox` or `.nox` is at most `unsupported` and carries the `vendored` facet, and the harness inventory still lists it with the fact `vendored: true`. The promotion journal also refuses a nested path whose folder the project's ignore rules exclude, through a read-only `git check-ignore --no-index`; an unknown git answer fails closed. Their `CLAUDE.md`, `AGENTS.md`, `.claude/rules` and skill files could previously be journaled because those trees hold no `.git` of their own.

## 0.118.2

### Patch Changes

- 13f6656: `harness_inventory.join_loading` now counts a loading record once per `(case_id, event_id)`, so a capture read twice no longer inflates the counts. Repeats are reported as `duplicate_events` with a `duplicate_event_identity` warning, and `duplicate_event_conflict` when a repeat differs from the kept copy. `loading_evidence` is `none` unless a node has an exact attachment, and `attachment_shapes` is `none` when no attachment or hook output was counted.

## 0.118.1

### Patch Changes

- 982f729: Keep the 1 MiB runtime configuration bound in front of every decoder in multi-surface dispatch. The harness inventory now refuses a combined `.claude/settings.json` or `.codex/config.toml` over `MAX_CONFIG_BYTES` once, before any parser runs, recording `parse_failed` for the primary kind and every facet. The `config` parser also checks the bound itself, so an `.mcp.json` node over it is `parse_failed` too, and the hook, plugin and command parsers share the same constant.

## 0.118.0

### Minor Changes

- 81556a3: Add `agent_harness_lab/config_recommendations.py`, a recommendation-only report built from config inventory and harness inventory documents. It flags shadowed Claude MCP servers, duplicate Codex servers (precedence `unverified`), `.mcp.json` servers that no user settings approve, Codex servers in a project layer that is not declared trusted, and output styles without `keep-coding-instructions: true`. For each Codex `.rules` file it suggests a `codex execpolicy check` command for the user to run. Memory locations are reported only as present and excluded. The report opens no file, runs nothing and writes nothing. Text comes from fixed templates and facts are only labels and digests, so no secret value can appear. Every row has `adoption_authority: false` and `publication_allowed: false`.

### Patch Changes

- efd8662: Config recommendations now refuse an inventory whose `version` is not a label, a `declared` inventory whose version differs from `surfaces.PINS`, and a digest field that is not a SHA-256 value. No digest covers these fields, so a forged value could previously reach the report. `mcp_server_shadowed` now records `winner_approval` and is `unverified` when the winning `.mcp.json` server is rejected or not approved by user settings. The memory exclusion test now records `dir_fd`-relative opens, with a positive control.

## 0.117.0

### Minor Changes

- 6ca3e83: Add `agent_harness_lab/plugin_recommendations.py`, which turns a proposed change to an explicitly selected external plugin checkout into a reviewable patch bundle. Nothing is applied. `workflow.recommend_plugin_change` routes the change to private `0o700` state, where the bundle holds a git-format `patch.diff` computed against the recorded HEAD and a `manifest.json` with the HEAD commit and per-file hashes. The checkout must be pinned to the HEAD the plugin inventory recorded, and its worktree must equal the HEAD tree; otherwise the call returns a `refused` receipt and writes nothing. Worktree, committed-tree, Git-directory and Git-output bounds are checked before reading, so a tree DAG, an oversized configuration or a compressed object bomb is refused as `checkout_bound_exceeded`, and a `state` path containing `..` is refused. Commit and tree objects are read through the hardened `GitView` copy. `status()` reports `stale_patch` once HEAD moves. Enablement changes stay `recommendation_only`. Changed components are re-parsed with the registered parsers in a temporary copy, while runtime loading and compatibility stay `unverified`. No `claude plugin` or `codex plugin` command runs, and nothing is pushed or published.

## 0.116.0

### Minor Changes

- b897f84: Staging and adoption now cover nested `CLAUDE.md`, `.claude/CLAUDE.md`, `AGENTS.md` and `AGENTS.override.md` files, `.claude/rules/**/*.md`, `.claude/agents/**/*.md`, `.codex/agents/**/*.toml`, `.claude/commands/**/*.md` and nested `.claude/skills` or `.agents/skills` bundles, all through `surfaces.classify`. These rows are adoptable only when the caller confirms checkout containment, and only for non-executable paths they name. `candidate_states` checks a staged copy, and the promotion journal re-probes the project, so a file inside a nested repository stages but never adopts. Inventories and other lexical callers get the previous verdicts. Settings, `CLAUDE.local.md`, hooks, workflows, Codex `.rules`, MCP, memory and plugin checkouts stay non-adoptable. Default discovery still stages only root instructions and root skill bundles. Both proposer prompts list the allowed kinds from `surfaces.adoptable_surfaces()`. Configured `harness_paths` refuse `private_excluded` files. Change plans mark kinds without an adoptable row as unresolved, along with every hook-facet operation, so agent edits stay unresolved. The behavioral path still adopts nothing automatically.

### Patch Changes

- f02090e: Nested-surface adoption now checks what it writes, not only where. `candidate_states` and `Journal.prepare` classify a changed file with its execute bits, so an executable `.claude/rules/*.md` or a suffix-less script in a nested skill bundle needs the hook contract. An agent definition whose Claude frontmatter or Codex role file registers hooks also needs it, and an undecodable, escaped or unparsable definition fails closed. A rollback no longer breaks when a `.git` appears below the project after adoption, while a new promotion of the now-nested file is still refused.

## 0.115.0

### Minor Changes

- 13426c6: The pinned Claude Code 2.1.316 native profile now records `nested_memory` and hook-output attachments, `Agent`/`Task` `subagent_type` requests and `mcp__<server>__` tool prefixes as content-free `loading_signal` metadata (path, content, agent-name and MCP-server digests). These attachment shapes are unverified and backed only by synthetic fixtures. `harness_inventory.join_loading` joins them to inventory nodes. A node's `observed_loading` becomes `observed_pattern` only when both path and content hash match exactly, and `effective` stays `unknown`. Evidence for a path or name the inventory lacks is counted as unattributed. A missing capture leaves every node `unknown`, and Codex inventories record `no_native_loading_signal`.

## 0.114.3

### Patch Changes

- 922b250: The command, skill and prompt inventory now reads frontmatter only through `texts()`, a new text mode of the shared bounded reader in `sources/frontmatter.py`, and keeps no frontmatter logic of its own. `texts()` drops a leading BOM, returns `None` without a leading fence, `''` for an empty value and `None` for a list or mapping, and reports a space-padded fence as `unsupported_yaml`. `read()` and `string_lists()` are unchanged, and inventory verdicts are the same as before.

## 0.114.2

### Patch Changes

- df5da73: The harness inventory now offers `.claude/settings.json` and `.codex/config.toml` to every parser whose kind is among the file's classifier facets. Other files with a second facet, such as a `hooks/hooks.json` inside a skill bundle or an agent definition's hook facet, are still read by their primary parser only. The hook parser keeps the node's `parse_status` and plain facts. The config and new plugin-enablement parsers each record namespaced `config.*` and `plugin.*` facts with their own `parse_status`, so a failure in one parser no longer hides the others. `.claude/settings.local.json` carries the same facets but stays `private_excluded`, so no parser reads it. The plugin parser records counts only, so plugin ids, marketplace locations and secret values never reach a node.

## 0.114.1

### Patch Changes

- 6cb118f: The declared Codex instruction inventory now selects each project directory's instruction file by existence, as `openai/codex@d786ff91` `codex-rs/core/src/agents_md.rs` does. The first existing regular-file candidate (`AGENTS.override.md`, `AGENTS.md`, then the fallback names) is selected whatever its content. An empty selection is recorded as `unknown` with reason `selected_empty`, contributes no guidance or byte budget, and shadows the directory's later candidates, so an empty `AGENTS.override.md` now hides that directory's `AGENTS.md` instead of falling through. An untrusted project or a directory above the project root now marks empty files `out_of_scope` too. `CODEX_HOME` keeps its first-non-empty rule.
- 8649f74: The declared Codex instruction inventory now decides whether a project or `CODEX_HOME` instruction file is empty with Rust `str::trim` semantics, as `openai/codex@d786ff91` does in `codex-rs/core/src/agents_md.rs` and `codex-rs/codex-home/src/instructions/mod.rs`. Python `str.strip()` also removed U+001C to U+001F, so a file containing only those separators was recorded as `selected_empty` with no byte budget, although Codex loads it and charges its bytes. Such a file is now content: it is declared applicable, counts against `project_doc_max_bytes`, and a budget it exhausts blocks the receipt.

## 0.114.0

### Minor Changes

- 2983071: Cooperative Claude Code and Codex trials now copy each `harness_paths` file at its relative path at any depth and record `workspace_surfaces` (included paths with kind and SHA-256, absent paths, `observed_loading: "unknown"`). Each trial requests exclusion of user, local and global layers, gated on new `surfaces.CAPABILITIES` rows: Claude Code gets `--setting-sources project`, `--strict-mcp-config` with an empty staged MCP config and `--settings` with `disableAllHooks`, and Codex gets a temporary `CODEX_HOME` holding only the copied `model` and `model_reasoning_effort` and one trust entry for the trial workspace. The Codex home holds no credentials. Neither the argv nor the environment names the real `CODEX_HOME` or `~/.claude`. `results.json` replaces the free-text isolation admission with an `ahl-trial-surfaces-v1` descriptor. Its status is `inherited_unverified` while the rows are unverified, and it never says `isolated`. The controlled Responses executor accepts an optional task `cwd` and loads the root-to-`cwd` `AGENTS.md` chain in order. A chain over 32 KiB blocks instead of being truncated.

### Patch Changes

- 4a7d49c: A Codex CLI evaluation now blocks as `codex_credentials_unavailable` before any trial when the binary reports the `codex-cli` banner and `CODEX_API_KEY` is not set, because the credential-free temporary `CODEX_HOME` cannot reach a saved `codex login`. `results.json` records `runtime.codex_authentication` (`environment_variable_unobserved`, `unavailable`, `unknown` or `inherited`). The Claude Code trial descriptor now lists user and local permission rules, sandbox settings, `env` and `apiKeyHelper` as excluded by `--setting-sources project`, and inherits only project and managed permissions. The execution reference, workflow guide and help text now say to put trial permissions in the fixture's `.claude/settings.json`.

## 0.113.0

### Minor Changes

- ef6fa40: Add `agent_harness_lab/sources/config_inventory.py`, a presence-only inventory of runtime configuration. It covers Claude `.mcp.json`, project and user `settings.json` and output styles, Codex `.codex/config.toml` MCP servers and instruction-replacing keys, and memory locations. Rows hold only vocabulary labels, booleans, counts and digests, so command, argument, URL, env, header and token values are never recorded. Memory locations and `.claude/settings.local.json` are `private_excluded`: their presence comes from `lstat` and they are never opened. Roots outside the checkout are read only when declared by role. Codex project servers stay trust `unknown` unless the project is declared trusted. MCP server digests match `telemetry_server_sha256` for `mcp__` tool names. `surfaces.RULES` adds `recommendation_only` config rows for the config files and `private_excluded` rows for memory stores, while the hook rows keep `.claude/settings.json` and `.codex/config.toml` `contract_required`. In the surface walk, `.mcp.json` and output styles are `config` nodes. `.claude/settings.json` and `.codex/config.toml` keep the `hook` kind with a `config` facet, so the hook parser owns them; 0.114.2 later let the config parser read them through multi-surface dispatch. `.claude/settings.local.json` is presence-only and is never read.

### Patch Changes

- 976a3be: Fix the config inventory found in review. A deeply nested `.codex/config.toml` is now `parse_failed` under the same depth bound as JSON, instead of a `RecursionError` that crashed the inventory and the surface walk. A settings file at a runtime path is always inventoried, even when it carries AHL's derivation `profile` key; AHL's derivation snapshot stays excluded by its path alone. The memory audit test now also detects reads by path string.

## 0.112.0

### Minor Changes

- bbb535d: Add `agent_harness_lab/sources/plugins_inventory.py`, a read-only inventory of plugin and marketplace enablement. It reads Claude Code `enabledPlugins` and `extraKnownMarketplaces` from `.claude/settings.json`, and reads `.claude/settings.local.json` only on opt-in. For Codex it reads `[plugins."name@marketplace"]` and `[marketplaces.<name>]` from `.codex/config.toml`. An enabled plugin stays `content_not_selected` unless the caller selects a separate Git checkout for it through `plugin_checkouts`. That mapping is also an optional `ahl.json` key, capped at 32 absolute paths. A selected checkout gets the harness inventory walk with the plugin-root layout and its own `HarnessSnapshot` at the recorded HEAD. It is marked `external_repository`, and `surfaces.classify(path, origin="external_repository")` returns `recommendation_only` for every path in it except `.git` and cache paths, which stay `private_excluded`. A Codex config nested deeper than the JSON depth bound raises `ValueError`. Its components use the registered parsers; this release adds the `plugin` manifest parser. A plugin-root `CLAUDE.md` is marked `not_loaded_by_runtime`, a hooks file missing its `hooks` wrapper is flagged, and the manifest follows each runtime's pinned search order. No plugin cache, plugin or git command, or network is used.

## 0.111.0

### Minor Changes

- 7b4a701: Add `agent_harness_lab/sources/commands_inventory.py`, a read-only inventory of Claude Code slash commands, nested and ancestor skill roots, and Codex custom prompts. It builds on the harness inventory. Commands under `.claude/commands` get `a:b` ids and are flagged `shadowed_by_skill` when a skill has the same id. Their `name` and `paths` frontmatter keys are flagged as ignored. Nested `<subdir>/.claude/skills` are `on_demand`, and only a nested skill whose directory name clashes with another skill gets a `/<subdir>:<directory>` id, with the subdirectory relative to `task_cwd`. Emitted ids are derived from paths only: a frontmatter `name` that differs from its directory is flagged `id_withheld`. Codex `.agents/skills` from `task_cwd` up to the checkout root are all listed, and skills with the same name are flagged, never merged. Name, depth and unverified description limits are flagged, `agents/openai.yaml` presence is recorded, and `[[skills.config]]` disables are applied only from a caller-declared config. Codex prompts are read only from a caller-declared `CODEX_HOME` and are labelled `deprecated` and `loader_unverified_at_head`. Nodes the harness inventory leaves unread are skipped, and harness change plans still list command files as unresolved. Every entry keeps `observed_loading` and `effective` as `unknown`.

## 0.110.0

### Minor Changes

- 129378e: Add `agent_harness_lab/sources/agents_inventory.py`, a declared inventory of Claude Code `.claude/agents/**/*.md` and Codex `.codex/agents/**/*.toml` custom agent definitions, plus caller-declared user, plugin and `CODEX_HOME` roots. A new bounded frontmatter reader, `sources/frontmatter.py`, reports Claude skip reasons (`no_leading_fence`, `bad_yaml`, `missing_name`, `invalid_name`, `missing_description`), keeping `bad_yaml` for syntax every YAML parser rejects and `unknown` for valid YAML outside its subset. It flags unknown fields as `ignored_by_runtime`, checks the `omitClaudeMd` and `autoCompactWindow` version gates against the declared version, and marks plugin-only ignored fields as `ignored_for_plugin_agents`. Codex role files are parsed with `tomllib`. They need a name, a description and non-blank `developer_instructions`. Unknown keys are `unknown_field_rejected`, and `sandbox_mode`, `approval_policy`, `model_provider` and `mcp_servers` are `no_override_effect`. Same-layer duplicates are warned, a role without a description inherits one from a lower-layer role and wins, an untrusted project layer is `gated`, and unknown project trust gives `depends_on_project_trust` wherever trust changes the outcome. Overly nested TOML is reported as `unknown` instead of crashing the capture. Precedence labels are declared only: no definition is reported as available or loaded, and load timing stays `unverified`. Agent files, including those below a `hooks` or `skills` folder, now classify as kind `agent` with the hook contract facet, so their changes are never adoptable and embedded hooks are listed for hook classification instead of being executed, with an `unparsed_hooks` placeholder when a file that mentions hooks cannot be read.

## 0.109.0

### Minor Changes

- 7296a87: Add `agent_harness_lab/sources/workflows_inventory.py`, a static inventory and heuristic lint of Claude Code workflow scripts (`.claude/workflows/*.js`). Scripts are never executed: a bounded standard-library lexer skips comments, strings and template text, and it checks that `export const meta = {` is the first statement and holds a plain literal with `name` and `description`. Anything else is `meta_not_first_literal`. The lexer also counts `import(`, `Date.now(`, `Math.random(` and no-argument `new Date` in code and records `phase()` titles. Results are labelled `static_lint_not_runtime_validation`, and a lexer doubt is `lint_inconclusive` with null counts, never a pass: a `/` after `}`, `++` or `--` is `regex_or_division_ambiguous`, an out-of-range `\u{...}` escape is `invalid_escape`, and a malformed script never fails the whole inventory. Project scripts on the `task_cwd`-to-root path follow the documented precedence: the closest directory wins, project beats personal and plugins are namespaced. Undocumented ties are `clash_unresolved`. Personal and plugin directories are read only when the caller declares them. `.claude/workflows/*.js` now classifies as a `contract_required` `workflow` surface, even beneath a `hooks` or `skills` directory, the harness inventory registers the lint as its `workflow` parser, and the capability table records the 2.1.248 and 2.1.269 gates. Codex has no workflow loader and returns `unsupported` with that evidence.

## 0.108.0

### Minor Changes

- 08e9bd3: Add `agent_harness_lab/sources/hooks_inventory.py`, a static hook inventory. It parses the hooks key of `.claude/settings.json`, plugin `hooks/hooks.json`, Codex `.codex/hooks.json` and the `[hooks]` table of `.codex/config.toml` as data only. Each handler records its event against the pinned event lists, its matcher kind (`exact`, `pipe_list`, `regex` or `all`), its handler type, timeout and `onFailure`. A command resolves to a hashed `referenced_script` node or to `external_or_dynamic_command`. Codex `prompt` and `agent` handlers are `parsed_but_skipped`. Unknown events and fields, a missing plugin wrapper, and both Codex representations in one layer are flagged. `.claude/settings.local.json` is `private_excluded` and never read. Other settings keys, env and header values are never recorded, only digests. Native runtime, Git developer and ordinary-script hooks are distinguished. No hook command or script is executed or imported, and `observed_execution` is always `unknown`. The `surfaces` table classifies Claude settings and Codex hook config as `contract_required` hook surfaces, and Git hook managers as `recommendation_only`.

## 0.107.0

### Minor Changes

- 0b6bac1: Add `agent_harness_lab/sources/claude_rules.py`, which classifies Claude Code path-scoped rules (`.claude/rules/**/*.md`, nested rules directories included) against declared task paths and setting sources: `launch`, `on_demand_subdirectory`, `conditional_matched`, `conditional_unmatched`, `conditional_unknown` or `skipped_setting_sources`. A new bounded, standard-library frontmatter reader accepts only a `paths` YAML list or flow sequence of strings. Any other construct is `frontmatter_unsupported`, and brace expansion past 256 patterns is `blocked`. Symlinked rules are refused as `symlink_not_followed`. No rule is reported as loaded: `observed_loading` and `effective` stay `unknown`. The harness inventory registers a `path_rule` parser and classifies Codex `.codex/rules/*.rules` files as `exec_policy`. They are `recommendation_only`, read once for a digest and never evaluated, and `codex execpolicy check` is recorded as their native validator. Selected rule files in the Claude instruction receipt now name their frontmatter instead of `rules_not_modeled`. Glob dotfile, case, bracket and negation semantics are marked unverified.

### Patch Changes

- 703460c: Bound hostile Claude path-scoped rules. A pattern of more than 64 path segments is now `blocked`, brace groups are found in one linear scan, and glob matching no longer recurses, so a rule file can no longer raise `RecursionError` or burn minutes of CPU during capture or inventory. A `.md` file below `.claude/rules/` is now kind `path_rule` even when its path contains a `skills/` directory, so such rules are no longer dropped from the rules receipt.

## 0.106.0

### Minor Changes

- 5b82622: Inventory nested instructions across the whole checkout. The Claude Code profile now walks the checkout without following links or entering nested repositories. Every `CLAUDE.md`, `.claude/CLAUDE.md`, `CLAUDE.local.md` and `AGENTS.md` is classified against `task_cwd` as a launch ancestor, `on_demand_subdirectory` (its imports are resolved but stay `unknown`), a shadowed `AGENTS.md`, or `outside_task_scope`. `CLAUDE.local.md` is always `private_excluded`: it is hashed but never admitted, expanded or output. The optional declared `instruction_files` mode and `claude_md_excludes` patterns are inputs; an unknown mode leaves every project file `instruction_files_setting_unknown`, and exclude matching is labelled unverified. A walk over 100000 entries or depth 64 is `blocked`. Codex captures accept a caller-declared `CODEX_HOME`, where the first non-empty `AGENTS.override.md` or `AGENTS.md` applies. They also accept declared `project_root_markers` (`[]` limits discovery to the cwd) and `project_trust: declared_untrusted`, which drops project files. `CODEX_HOME` is never read unless declared. The surface inventory classifies nested instruction files as `recommendation_only` and registers an instruction parser that reports path-free relation, trigger and size facts, never loading evidence.

### Patch Changes

- 765e672: Keep the whole-tree Claude Code instruction walk bounded and non-fatal. Files are hashed while streamed, so outside-scope and `CLAUDE.local.md` files no longer stay in memory, and an expanded file is re-read, at most 4 MiB of it. An unreadable subdirectory or instruction file becomes an `unknown` node (`instruction_directory_unreadable` or `instruction_file_unreadable`); the receipt is `blocked` only when it sits on or below the task directory. An on-demand import through a link, a nested repository or an unreadable path becomes an unresolved node instead of failing the capture. An oversized on-demand file is no longer skipped silently: following the documented truncation, its imports are still resolved, the file is marked `on_demand_truncation_unverified`, and imports past the smallest documented 40,000-character limit are `import_beyond_on_demand_truncation_unverified`.

## 0.105.0

### Minor Changes

- 07c59f5: Add `agent_harness_lab/sources/harness_inventory.py` and `ahl sources inventory`, a read-only inventory of the harness surface files in one checkout. It walks the checkout without following links and refuses symlinks, special files, `.git`, nested repositories and hard-linked surface files. Every regular file that `surfaces.classify` recognizes becomes a node with its kind, scope, content hash, mode, declared state, parse status, trust and a capability reference. `observed_loading` and `effective` are always `unknown`. Node path identities match `HarnessFile`, and an optional Git `inventory_profile` binds the inventory to the captured `HarnessSnapshot` without changing its fingerprint. Bounds are 1000 nodes, 10 MiB, depth 32 and 20000 entries. Going over any of them gives `blocked`, never a truncated result. Roots outside the checkout are read only when the caller declares them, and those nodes are labelled `caller_declared_external`. Project trust is a declaration that defaults to `unknown`. Parsers register in a sorted `PARSERS` tuple and are all stubs in this release. The CLI writes to a private `0700` XDG state directory with `publication_allowed: false`.

## 0.104.0

### Minor Changes

- 5aad085: Add `agent_harness_lab/surfaces.py`, which now owns harness-surface classification and the writable allowlist. `classify(relative_path, runtime, mode)` returns the kind, scope directory, executable flag, writability (`adoptable`, `contract_required`, `recommendation_only`, `private_excluded` or `unsupported`) and plan facets. Candidate scope checks, journal ownership, skill-bundle discovery, root instruction snapshots and change-plan operations now call it, and their verdicts are unchanged: no new path becomes writable. The module also holds a frozen `CAPABILITIES` table pinned to Claude Code 2.1.317 and Codex 0.166.0 (`openai/codex@d786ff91`), with public doc URLs. Items the 2026-11-05 research did not confirm stay `unverified`, and a row without a doc URL, the runtime pin or a status is rejected. A characterization table pins today's verdicts for every caller.

## 0.103.2

### Patch Changes

- 89e4222: Add a synthetic native-capture walkthrough to the telemetry contract. A committed four-row `claude-jsonl-v1` fixture shows what normalization keeps and what it leaves out: bodies are omitted, a tool error stays `execution_status: error` with an unknown outcome, a missing result becomes `unmatched_call` coverage, and state and input/result fingerprints stay absent. The page says normalization is not general sanitization and that compatibility with released CLIs is unverified. A test runs the documented snippet and ingests the fixture into a ledger, and fails if either output stops matching the page.

## 0.103.1

### Patch Changes

- 586815c: State the improve/watch recommendation boundary in CLI help and the README. Top-level, `ahl improve --help` and `ahl watch --help` now say what each command changes (private run artifacts, a run lock and, for watch, its checkpoint) and what AHL itself never writes (project instructions, skills, hooks and other harness files). The help also says the agent each command starts is cooperative: read-only project access is instructed, not enforced, and AHL rejects a stale result after a concurrent project change without reverting it. `--repair-contracts` is named as the only path that may apply a bounded skill-header repair, which a project `ahl.json` keeps recommendation-only. Watch help now says it reruns on changed evidence or execution configuration, not only on changed evidence. Command behavior and runtime gates are unchanged.

## 0.103.0

### Minor Changes

- 8d5c2a0: Add an offline `local_file/cognee-export/v1` source for one selected Cognee v1.6.5 graph export of one dataset (`GraphSnapshot.save()` JSON or the `format="json"` nodes/edges file). Documents, chunks, entities, other nodes and edges become separate hash-only claims namespaced by instance, dataset label and dataset ID, each with an origin-document hash that resolves to the document's claim; only edges whose name and endpoint types match a pinned model field count as native structure; an export naming another dataset is refused, and a file without a dataset ID gets unknown lineage with cross-source joins disabled. Nothing imports Cognee, runs recall or inference, opens a store or mutates the graph, and the shape stays labelled unverified.

## 0.102.0

### Minor Changes

- 61544d6: Import one extracted Hindsight v1.1.0 bank transfer archive offline with the `local_file/hindsight-bank-archive/v1` source. Retained documents, extracted facts, causal relations, consolidated observations and curation-archive (invalidated) facts become separate hash-only claims. Each claim keeps its record time apart from its validity window, and its assertion status (current, historical, not yet valid, invalidated or undetermined) is judged at the archive's export time. An archive for a bank other than the selected one is blocked before any document is read. Recall, reflect, retain and any network or service call are refused. Compatibility stays labelled unverified, and no claim becomes a task outcome, agent defect or independence label.

## 0.101.0

### Minor Changes

- 8d97cc7: Add the offline `mem0-oss-export` source for one selected Mem0 OSS v2.2.1 memory and history export. Memories and history rows become hash-only contextual claims: `ADD`/`UPDATE`/`DELETE` events are versions and tombstones, superseded, expired and deleted memories stay in evidence but leave the currently-asserted count, and declared `user_id`/`agent_id`/`run_id` filters exclude other scopes. History for unlisted memories is excluded unless selected. Compatibility stays unverified, the hosted Platform v3 is unsupported, no server or network is contacted, and no memory becomes a task outcome, task root or independent case.

## 0.100.8

### Patch Changes

- 992665d: Count Claude-Mem search hits against the row bound. `max_rows` now bounds the records an export admits rather than its rows: each session row counts once, each observation, summary and prompt row twice (its claim and its `search_hit`), plus the one `claude_mem_export` record. The documented 100,000 ceiling previously admitted up to 200,001 records; it now admits at most 100,000. Rows later rejected for missing identity still count, because the bound is checked before rows are mapped.

## 0.100.7

### Patch Changes

- 7154dc1: Treat a pytest case whose finite setup, call and teardown durations sum to a non-finite total as an invalid duration. The case now gets `duration_seconds: null` with the `invalid_duration` warning, as the source contract documents, instead of the whole test-results capture being refused with `invalid test duration`.

## 0.100.6

### Patch Changes

- 390a2c6: Keep GitLab merge requests whose source fork was deleted. GitLab sets `source_project_id` to null once the source project is deleted; the merge request is now recorded with `source_project_state: "unknown"` and a null `source_project_sha256` instead of failing the whole capture as an integrity error. An approver whose `user` object is missing or has no `id` is counted in `approvers_unidentified` instead of failing the capture, as documented; a `user` that is not an object, a non-positive ID or a boolean ID is still refused.

## 0.100.5

### Patch Changes

- 5ebdddd: Refuse special files before opening the leaf. `open_nofollow` now runs `lstat` on the final name relative to its walked parent and refuses anything but a regular file before the open, so an allowlisted file that is already a device node is never opened (a leaf swapped after the check is still caught by the post-open `fstat` of readers) and a Unix socket raises the labelled `ValueError` (`artifact must be a regular single-link file` for artifact extraction, `input must be a bounded single-link regular file` for other reads) instead of a bare `OSError`. The descriptor is still checked with `fstat` after the open. FIFO test cases now run on a thread with a timeout, so a blocking open fails the test instead of hanging the suite.

## 0.100.4

### Patch Changes

- 92d9be6: Create, lock and open root directories through the no-follow component walk, so a parent directory swapped for a symlink after the caller's checks is refused instead of followed. The new `open_beneath(path, flags, mode=0o600)` reuses the walk and opens the final name relative to its parent with the caller's flags, always adding `O_NOFOLLOW | O_CLOEXEC`. Private journal file writes use it. A locked journal opens its store once through the walk, creates `.lock` relative to that held store descriptor, and publishes its marker and events from that descriptor after re-walking the store path; a store replaced while locked raises the new `case store changed while locked`. Project configuration creation, working-state capture, analysis snapshots and publication, runtime image scans and the delegated cgroup root open their directories with `open_directory_nofollow`. Exclusive-create and lock behaviour and permissions are unchanged, and existing messages are kept; a swapped ancestor, or a symlinked final component that used to raise `OSError` (`ELOOP`), now raises `input crosses a symlink`.

## 0.100.3

### Patch Changes

- 6d8c690: Route the remaining full-path no-follow opens through the component walk, so a parent directory swapped for a symlink after containment is refused instead of followed. Bank revision tree fsyncs, regression case evidence fsyncs and lineage snapshot fsyncs now open files with `open_nofollow`, and `sync_directory` opens directories with the new `open_directory_nofollow`, which reuses the same walk and opens only the final component `O_RDONLY | O_DIRECTORY` so it can be fsynced. Error messages and call-site signatures are unchanged; a swapped ancestor raises `input crosses a symlink`.

## 0.100.2

### Patch Changes

- dde63c3: Open contained reads one path component at a time so a parent directory swapped for a symlink after the containment check is refused instead of followed. `read_regular`, `regular_sha256`, `regular_size`, `regular_equal`, `tree` and `artifacts` now walk from `/`, opening each directory by name from its already opened parent with `O_DIRECTORY | O_NOFOLLOW` (`O_PATH` on Linux, so search-only directories still resolve) and the leaf with `O_NOFOLLOW | O_NONBLOCK`, then keep the regular-file, single-link and byte-bound checks on the final descriptor and the 1 MiB streaming chunks. Every intermediate descriptor is closed on all paths. Platforms whose `os.open` lacks `dir_fd` support refuse these reads. Call-site signatures are unchanged.

## 0.100.1

### Patch Changes

- 33617d5: Bound scalar values in hostile JSON exports before decoding. The shared screen for pytest JSON reports and Claude-Mem exports now also counts strings, numbers and literals outside object keys, in the same streaming pass that skips string contents, against a new `max_json_values` ceiling that a selection may only lower: 2,500,000 for pytest JSON and 4,000,000 for Claude-Mem. Measured with `tracemalloc`, a decoded value costs up to about 90 B and a container up to about 150 B, and both budgets can fill in one document, so the bound is their sum: under about 360 MiB for pytest JSON and 490 MiB for Claude-Mem at these ceilings. Object keys are not counted, because each key has a counted value, so an object of many unique short keys still peaks at about 20 to 24 times its size, bounded only by the byte limit (`max_report_bytes` or `max_export_bytes`). An unterminated string is now refused in one pass instead of being rescanned from every later quote. The loader, `limits` and `HostileReport` move to `agent_harness_lab/sources/hardened.py` and stay importable from `sources.test_results`, and both sources share one read, hash, re-hash and selection check.

## 0.100.0

### Minor Changes

- 77b9abd: Add a read-only `api/gitlab/v4` source for selected issues and merge requests of one explicitly named project on GitLab.com or a self-managed instance. The selection names a canonical HTTPS instance root (sub-paths allowed), a numeric project ID or a literal `group/subgroup/project` path (sent once URL-encoded as the `:id`; `..`, percent-encodings and other escapes are refused), the issue and merge request IIDs and what to capture (`approvals`, `issues`, `merge_requests`, `notes`). Requests are GET only and nothing is enumerated. Records key on the host, the numeric project ID returned by `/projects/:id`, the object kind and the IID, so issue 7 and merge request 7, and the same IID in another project or on another host, stay distinct; the global `id` is kept separately, and a fork's `source_project_id` keeps its own project identity. The server version comes from `/version` or is `unknown`. Issues, merge requests (source and target project and branch, `sha`, `merge_commit_sha`, `squash_commit_sha`, state, `merged_at`, `draft`), user notes, system notes (a separate `gitlab_system_note` record) and approvals are hash-only records, and a merged, closed or approved merge request never becomes a verified task outcome. Note listings use offset `page`/`per_page` with `X-Next-Page`, `X-Total` and `Link`; incomplete pages, 401/403/404/429 and spent budgets block the checkpoint, while a missing approvals endpoint is an `unsupported_endpoint` gap. `ahl sources` accepts the GitLab configuration with `--authorization-env NAME` or `--authorization-file`, prints a `gitlab_summary` on preview and exits 3 when an import is blocked. The Gitea reader now runs on a shared capture ledger and shared transport helpers for budgets, headers and `Link` pagination checks, which the GitHub reader also uses. Threaded discussions, MR commits and changes, linked issues and pipeline evidence remain open.

## 0.99.0

### Minor Changes

- ae4c73c: Add an offline `local_file/claude-mem-export/v1` source that reads one selected Claude-Mem JSON export (the `scripts/export-memories.ts` output) pinned to upstream release v15.9.0, commit `d0968e5d`. Other declared releases are refused, as is any version field in the export that disagrees with the declared one. The export carries no version of its own, so the release stays caller-declared and the profile is marked `compatibility: unverified`. Observations, generated summaries, user prompts, memory sessions and the query's search hits become separate hash-only `contextual_assertion` claims, namespaced by a declared instance label. A summary is a derived session synthesis, never an observation or an independent case. A search hit points at its target claim and is not evidence of use. No claim becomes a verified task outcome. Unknown fields, rows without an ID, missing or conflicting timestamps, duplicate IDs, total mismatches and unscoped exports are counted or warned, never dropped silently. Bodies, session IDs, projects and the query are fingerprinted only. `ahl sources preview` prints a `claude_mem_summary`, and profile `claude-mem-export-v1` is rejected by public export. The source never contacts the worker or MCP server, opens the SQLite database or touches the network. The test-result source's bounded JSON loader is now shared as `load_json`. Worker/MCP reads, a SQLite snapshot mode, `tool_uses` and cross-journal duplication checks remain open.

## 0.98.0

### Minor Changes

- 15d8946: Add a read-only `api/gitea/v1` source for selected issues and pull requests of one explicitly named repository on a self-hosted Gitea instance. The selection names a canonical HTTPS instance root, `owner/repo`, the issue and PR numbers and what to capture (`comments`, `issues`, `pulls`, `reviews`); `http://` and non-canonical roots are refused, and nothing is enumerated. Identity is the instance host, port and path plus the owner/repository name and the issue/PR number or comment/review ID, so the same number on another host or repository stays distinct and a fork head keeps its own repository identity. The server version is recorded from `/api/v1/version` or marked `unknown`. Issues, PRs (base/head repository, branch and SHAs, state, draft, merged and merge commit), comments and reviews (state, commit, stale/dismissed/official) are hash-only records, and a closed issue, merged PR or approval never becomes a verified task outcome. Listings use `page`/`limit` with `Link` and `X-Total-Count`; incomplete pagination, 401/403/404/429 and spent budgets are explicit coverage states that block the checkpoint, while a missing review endpoint is an `unsupported_endpoint` gap. `ahl sources` accepts the Gitea configuration, a new `--authorization-env NAME` option or the existing `--authorization-file`, prints a `gitea_summary` on preview, and exits 3 when an import is blocked. Non-200 responses now raise `HTTPStatusError`, a `TransportError` carrying only the status code. Inline review comments, PR commits and files, timelines and CI evidence remain open.

## 0.97.0

### Minor Changes

- 969aab6: Add an offline `local_file/test-results/v1` source that imports selected JUnit XML and pytest JSON reports bound to a caller-declared full commit SHA (40 or 64 hex). Each test case becomes a hash-only `test_case_result` development-artifact record with status `passed`, `failed`, `error`, `skipped` or `unknown`, plus the native outcome label and duration when present. Conflicting, rerun, expected-failure and unrecognized outcomes stay `unknown`, and duplicate case names stay as flagged occurrences. Results are CI evidence about the declared commit only: `commit_verified` is false, causal status and independence stay unknown, no task outcome is verified, and profile `ci-test-results-v1` is rejected by public export. Hostile-input handling uses only the standard library. DOCTYPE/ENTITY and other markup declarations, non-UTF-8 encodings and NUL bytes are refused before parsing. Bounded `XMLPullParser` chunks abort early on element, depth, attribute and case limits, and pytest JSON rejects duplicate keys, non-finite numbers, deep nesting and invalid schemas. `ahl sources preview` reports a `test_results_summary`. GitHub Actions run ingestion, artifact downloads and commit joins remain open.
- Bound pre-decode memory for hostile test reports: pytest JSON nesting and container counts are screened before decoding (new `max_json_containers` limit), JUnit start tags are checked against the attribute bound before expat builds them, finished XML elements are released while parsing, and report bytes are no longer retained for the change check. Status-like JUnit testcase attributes and pytest passes with a non-passing phase now map to `unknown` instead of `passed`.

## 0.96.0

### Minor Changes

- 4525e96: Map declared skill evidence in pinned Claude Code 2.1.316 captures. A `Skill` tool call with a nonempty `skill` name adds `skill_signal: declared_invocation` to its `tool_call` event, and a `Read` of an exact `PROJECT/{.agents/skills,.claude/skills,skills}/<name>/SKILL.md` path adds `skill_signal: file_read`. Each carries a pseudonymous `skill_id` and a new `skill_signal_confidence` label (`declared_name`, `full_read_request` or `partial_read_request`); no name, path, argument or content is copied. The `skill`/`file_path` input field names are an assumption from Claude Code's public tool interface, not from the pinned SDK reference. The adapter capability is `skill_activation: False` with `skill_signal: observed_declared`. These are requests, not activation: `evaluation.skills.activation` never treats them as activated, and outcomes stay unknown. Captures without such calls keep identical events, identities, collection fingerprints and summaries. Codex adapters are unchanged.

## 0.95.2

### Patch Changes

- f5cbb36: Document which upstream ai-memory session-observation fields are normalized. Pin with tests that titles, bodies, body tool headers, extension events and unrecognized row fields never become tool names, call IDs or outcomes.

## 0.95.1

### Patch Changes

- bd2ffd7: Record the private skill invocation context (`invocation.json`, origin `skill`, `execution_authority=False`) as soon as an `ahl skills invoke` run directory exists, including runs that the configuration preflight blocks before evaluation and `--repair-contracts` runs. The receipt parity contract now requires `invocation.json` in the skill run and its absence from the CLI run. Receipts, statuses, exit codes and the `ahl improve` route are unchanged, and the context grants no authority.

## 0.95.0

### Minor Changes

- dc0d039: Add a receipt parity contract (`ahl-entry-receipt-parity-v1`, `agent_harness_lab/entry_parity.py`) between `ahl improve PROJECT` and `ahl skills invoke PROJECT`. For the same project, frozen inputs and runtime, both routes return the same exit code, stdout receipt and `workflow.json` keys and values, including status, recommendation, the `decision` block, the change plan and prerequisites. The only allowed differences are the private run directory, digests that bind run-local timing or invocation context, measured durations and remaining budget, the `origin` and `parent_run_id` given to preparation children, and the skill-only `invocation.json`. Stderr matches apart from the run directory and elapsed times, and a managed-preparation marker denies both routes with identical output. Receipts, statuses, exit codes and gates are unchanged, and the contract grants no authority.

## 0.94.1

### Patch Changes

- 54a2718: Fix the default `ahl improve` single-run `decision` block so it can never change a run's outcome. It no longer hashes the whole evaluation tree. That hash raised on routine trial-workspace symlinks (virtualenvs, `node_modules/.bin`) and turned `no_change` or `evaluation_failed` runs into `invalid_input`. The block now binds bounded `results.json` and manifest hashes, recording `unknown` when they cannot be read. A failure to build the explanation yields `{"status": "unavailable"}` and leaves the result unchanged. The block also reports `difference_outside_gate_splits` when only held-out or undeclared splits differ. It takes splits from the manifest, so a trial reporting another split counts as unknown with a `trial_split_mismatch` reason. It records `final_attempt` and `describes_final_attempt`.

## 0.94.0

### Minor Changes

- 6f14c5f: Default `ahl improve` runs now add a private `decision` block (profile `ahl-single-run-decision-disposition-v1`) to `workflow.json` after any exactly covered single-run evaluation. It explains why that evidence cannot support a recommendation: `disposition` is `insufficient_evidence` (or `invalid_evidence` when frozen inputs changed), and `next_evidence` is `requires_repeated_paired_experiment`. It reports observed per-split pair counts, keeping non-pass/fail outcomes unknown, and labels the observed pattern without diagnosing it. It also lists the unmet reasons: one repeat per task and arm, no frozen plan, a descriptive profile, unverified roots, isolation qualification from the live prerequisite scope and unverified mandatory certification prerequisites. It binds the latest attempt's evaluation, manifest, baseline and candidate hashes. Statuses, exit codes, the `recommendation` string, `application_eligible`, `automatic_promotion` and every existing gate are unchanged, all authority fields stay false, and public benchmark export rejects the profile.

## 0.93.0

### Minor Changes

- 05bbc19: Add `sources.memory_provenance.bind_provenance`, an offline API that relates declared `Handoff` and `Workstream` concept records in an authorized ai-memory OKF bundle to the session and page records already imported from that bundle. Links bind only on exact identifiers inside the declared project and workspace scope, and only while the bundle still equals the journal's latest checkpoint. They become `candidate` `JoinReceipt`s with the new `handoff_from_session`, `memory_links_session` and `memory_links_page` relations. Missing, ambiguous, cross-scope, self-referencing and malformed links stay `unlinked` with reasons. Duplicate or invalid record identifiers emit no links. Each record's `created_at` becomes a `TemporalFacet` knowledge time, and naive or missing times stay unknown. Upstream ai-memory documents no OKF handoff or workstream type, so the shape is caller-declared and reported as such. Reports hold fingerprints, receipts and counts only, never bodies, identifiers or paths. They write nothing, claim no delivery, injection, native session correlation or independence, and are rejected by public benchmark export.

## 0.92.1

### Patch Changes

- 6056c90: Correct how declared Claude source order is reported and attributed. A case whose records span files has unknown capture order, but valid strictly increasing timestamps can still order it, so the receipt now separates `cross_segment_unordered_cases` from `cross_segment_timestamp_ordered_cases`. Recurrence and matching coverage report `cross_file_capture_order_unknown_cases` and `cross_file_unordered_cases` instead of `cross_file_cases`, and the Markdown report states both counts. The `claude-native-order-v1` profile is accepted only on pinned `claude-jsonl-2.1.316` Claude records. The `source_order` summary records `declared_by: native_adapter` for the pinned adapter and `declared_by: caller` for normalized ledgers such as exported `events.v2.jsonl` files. The collection fingerprint now leaves out the source-order fields only for the pinned adapter; for caller-supplied ledgers it includes them, so a ledger that changes only its subordinals changes the fingerprint. Selection and output are unchanged for inputs without these fields.

## 0.92.0

### Minor Changes

- 629beaf: Add a versioned within-line source order to pinned Claude Code 2.1.316 captures. Several events from one native line (content blocks plus response usage/stop facets) used to share one `capture_order`, so method selection excluded those cases. Each event now also records `source_order_profile: "claude-native-order-v1"`, `source_suborder` (the content-array position, with usage and stop facets after the line's blocks) and `source_order_segment` (the physical file). Within one file and profile, method selection treats a strictly increasing `(capture_order, source_suborder)` as declared capture order. It reports these cases in `method_selection.inputs.source_order` and in the recurrence and matching coverage, and lists ties without a usable subordinal and cases that span files as still unordered. Records spanning files of a project-root journal stay unordered even when their offset ordinals increase. The subordinal is not execution or causal order and adds no outcome or independence label. Event IDs, journal record identities and revisions, collection fingerprints and duplicate-UUID checks ignore the new fields, so existing values are unchanged. Inputs without the fields keep identical analysis output and receipts. The schema rejects partial declarations and unknown profiles.

## 0.91.1

### Patch Changes

- 7a98385: Fix `agent_harness_lab.optimization.router.run_queue` evaluating `tool_adapter` mechanisms without adapter authorization. The queue now checks each record's component through `route()`, sharing the held dispositions used by `dispositions`. An unauthorized adapter record is deferred as `adapter_route_not_authorized`: its evaluation callback is not invoked and no budget is charged. Pass `authorized_adapters=True` to evaluate adapter routes. Queues without adapter routes keep the same order, deduplication, budget charges and outcomes.

## 0.91.0

### Minor Changes

- 737463a: Add `agent_harness_lab.diagnostics.mechanisms.from_proposal`, which admits untrusted proposer `mechanisms`/`falsifiers` JSON as unverified `MechanismRecord`s. It is pure and total over the proposal and returns a private `proposer-mechanism-admission-v1` receipt with each admitted entry (`proposed_unverified`, falsifier attached per mechanism) and every rejected location with a reason: malformed or unknown fields, authority claims, components or severities outside the router vocabulary, citations outside the controller's evidence allowlist, duplicate or contradictory mechanisms, several distinct falsifiers, and explicit, reported count/text bounds (`None` means unlimited; oversized input is rejected, never truncated). Admitted records keep disposition `proposed` and the unreviewed `insufficient_evidence` component. Add `agent_harness_lab.optimization.router.dispositions`, a deterministic plan of queue dispositions from separately reviewed routes that neither evaluates nor charges the budget and never yields validated or recommended. Both profiles are rejected by public export. `ahl improve` behavior is unchanged.

## 0.90.0

### Minor Changes

- 1eaebba: Add `agent_harness_lab.sources.temporal`, an offline view of which imported knowledge records were in force at a given instant, based on what each record declares. A `TemporalFacet` records a revision's knowledge time, declared validity interval, decision status and explicit supersessions. `resolve` reports `in_force`, `superseded`, `not_yet_valid`, `expired`, `not_adopted` or `unknown` with reasons, and `resolve_store` binds facets to one validated source journal and its revision edges. The default historical replay ignores corrections first recorded after the instant; `as_known_now` is an explicit retrospective view. Missing, unparseable or timezone-less times, supersession cycles, untied revisions and competing successors are reported as unknown instead of being resolved. Results are descriptive and private: they establish no truth or authority, and public export rejects the `knowledge-temporal-v1` profile. Existing record and journal fingerprints are unchanged.

## 0.89.7

### Patch Changes

- 51f1553: Analysis now holds less state per event. The recurring-sequence suffix automaton keeps its lengths, links and positions in parallel arrays, stores a single outgoing transition as a pair instead of a dict, releases transitions before counting roots, and keeps only each state's root count rather than a set for every state. Event references stay `(case_id, event_id)` pairs until they are emitted as evidence, observations replayed for method selection are value tuples instead of dicts, and cases whose capture order and timestamps have both become unusable drop their stored events because they can no longer be selected. Observation cache entries are tuples too. On a synthetic 192k-event corpus, peak traced memory fell from 2.3 KB to 0.6 KB per event. `analyze()`, `report()` and `ahl analyze` outputs, ordering, counts, findings and receipts are byte-identical.

## 0.89.6

### Patch Changes

- 713b33e: The evaluation runner now uses less peak memory. The input `digest` hashes each file through one reused 1 MiB buffer, so it no longer holds two copies of every file. `trace_result` classifies the agent stream in one pass and keeps only the flags, counts and usage it needs, not every parsed event. `results.json` is written with `json.dump` to a temporary sibling that replaces the file once complete, so the runner no longer builds the whole document as a string after every trial, and a serialization error still leaves the previous `results.json` in place. Digests, trace outcomes, `results.json` bytes, file modes and errors are unchanged.

## 0.89.5

### Patch Changes

- 43b8bfa: A source metadata cache with a byte limit no longer serializes the whole cache to check that limit before each commit. The new `atomic_json_size` helper counts the bytes `atomic_json` writes from the same encoder stream and stops once the limit is exceeded, so the check holds one chunk at a time instead of a string and a bytes copy of the cache. Ingest takes record identities from the records already validated on read instead of parsing each one again, keeps one dictionary per newly imported record, and checks metadata and graph references against the identity index without copying it into sets. The cache file format, the written bytes, the byte and record limits and every error message are unchanged.

## 0.89.4

### Patch Changes

- 1e2e9ae: Reads that only hash, size or compare a file now hold one chunk at a time instead of the whole file. New `regular_sha256`, `regular_size` and `regular_equal` helpers apply the same regular-file, symlink, single-link and byte-limit checks as `read_regular`. They are used for lineage store snapshots and the cache re-check, the normalized export readback, the closed source pin, correction, revision and oracle byte accounting, and the smaller manifest and receipt hashes. The benchmark input digest and the sandbox binary digest use `hashlib.file_digest`. Derivation snapshot pins feed `repr` into the hash in pieces, so they no longer build an escaped copy of every file. Digests, pins, error messages, limits and ordering are unchanged.

## 0.89.3

### Patch Changes

- 6d40b18: Native Codex and Claude captures use less peak memory. Decoded rows are released as soon as they are correlated instead of staying alive next to the normalized events, discovery drops its merge and conflict tables before building snapshots, pinned `collect` lets analysis free each event once it has been consumed, and the Claude projection sorts in place. Events still become visible only after the capture is re-verified. Records, summaries, digests, snapshots and journal bytes are unchanged.

## 0.89.2

### Patch Changes

- 48d3c62: The evaluation runner now splits agent JSON Lines traces on line feeds only, tolerating CRLF endings. A record whose string contains a raw U+2028, U+2029 or U+0085 parses as one record instead of being reported as `invalid_trace`, and U+000B, U+000C and U+001C to U+001E are no longer treated as record boundaries. Traces without those characters are classified exactly as before.

## 0.89.1

### Patch Changes

- 18790e0: Local Git capture now copies packfiles, pack indexes and loose objects into its private view in 1 MiB chunks, and hashes commit binary patches, object read-backs and the trusted Git binary incrementally. Memory no longer grows with the largest pack or patch: on a synthetic 128 MiB packed blob, peak traced Python allocation fell from about 350 MiB to about 2 MiB. Snapshots, summaries, digests, records and journal bytes are byte-identical, and the existing stamp, size, output and binary bounds and their errors are unchanged.

## 0.89.0

### Minor Changes

- 194eee2: Add a frozen schema v2 multi-class diagnostic triage policy in `agent_harness_lab.diagnostics.triage_policy`. Validation reports a root-grouped class-wise confusion matrix, per-class TP/FP/FN/TN with abstentions, abstention reasons, a label-free review queue bounded by the declared review capacity, explicit per-error-type costs, and severe misses as a separate count with hashed root IDs. It never selects thresholds. It refuses thresholds that are not human-preregistered and roots listed as fitting, calibration or threshold-tuning roots. Results stay `rejected` or `insufficient_evidence`, descriptive only, and private to the benchmark exporter. Schema v1 binary policy output is unchanged.

## 0.88.0

### Minor Changes

- 97f0204: Bind a closed live controller attempt's checked harness input tree to a clean local-Git `HarnessSnapshot` captured earlier in the same process from the same checkout location. The private receipt is `verified` only on an exact match, `mismatch` when the tree differs, and `unknown` for imported, late, relocated, dirty-at-capture or unowned inputs. Effective loading stays unverified, and no journal field, import label or authority is added.

## 0.87.2

### Patch Changes

- b67c104: Make the synthetic sequential history tests deterministic under host load. The fixture's 0.25-second arm and 3-second root quotas, not the 30-second history budget, were what a loaded host exhausted: a real grader subprocess timed out, the controller stopped the root as incomplete, and tests then failed with a misleading downstream error. The fixture now uses generous synthetic quotas, reports why a run did not seal (controller stop reason, journal reason, dispatched roles, trial statuses, elapsed against budget), and keeps root-quota and history-budget exhaustion covered by explicit tests that advance an injected clock. Production deadline enforcement is unchanged.

## 0.87.1

### Patch Changes

- 4dc32b6: Bound the Codex `0.166.0-v2` root walk to 128 directory levels and, by default, 250,000 visited entries with an iterative traversal; an optional `max_walk_entries` descriptor field (positive, at most 5,000,000) overrides the entry bound and is part of the selection digest only when present. Reaching a bound now reports a labelled `scan_depth`/`scan_entries` limit with an unknown `walk_limits` receipt and blocks the checkpoint instead of walking unbounded trees or crashing with a recursion error. In-bound scans are unchanged; the Claude subagent walk shares the same helper.

## 0.87.0

### Minor Changes

- 71b3721: Add an explicit bounded Claude Code `2.1.316-v2` project-root source profile. Map the declared project to the pinned reader's sanitized per-project directory inside selected roots only, admit files by native record scope, merge archive/prefix copies per session writer by native UUID, and report per-file accepted/rejected/unknown receipts without following links. The `2.1.316-v1` file profile, its identities and fingerprints are unchanged.

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
