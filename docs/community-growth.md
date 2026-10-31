# Community growth: evidence, decisions and 90-day plan

Prepared 2026-10-31. Growth epic: [#25](https://github.com/mihazs/agent-harness-lab/issues/25).
This is a maintainer proposal, not a record of external adoption. No outreach,
automatic schedule, model trial or promotional publication has occurred.

## Audit and evidence

Baseline main: `a2a3d3abb71c14ea4f384d8ef1cd4015f94e6104`; latest inspected release:
v0.2.0. GitHub reported zero stars/forks, empty topics, no homepage and Discussions
disabled. MIT license, security/support/conduct/contribution policies, templates,
Dependabot, an Ubuntu Python 3.11–3.13 test matrix, packaging CI and Changesets exist.
Only scientific tasks #4–#6 are closed; the implemented/pending distinction in
[scientific epic #3](https://github.com/mihazs/agent-harness-lab/issues/3) remains authoritative.

Confirmed friction: the old README led with authenticated improvement and contained
long workflow details before an offline demonstration; the original events fixture
showed transitions without a diagnostic finding. CONTRIBUTING lacked a file map,
specific task-selection path and newcomer communication guidance. CLI help still
has broad adoption wording ([#27](https://github.com/mihazs/agent-harness-lab/issues/27)).
These are source observations, not user-interview findings.

The new [quickstart](getting-started.md) was run in a clean Python venv without pip
on Linux/Python 3.12.14. It produces a checked-in report and does not use inference.
No live network release installation, macOS/Windows onboarding, independent usability
test or real-agent effectiveness study was performed. CI is a separate, commit-specific
gate. Browser visual rendering, repository security-setting state and traffic analytics
were not verified. A permissions object reporting admin does not provide an administration
mutation tool.

| Evidence class | Source | Decision and limit |
| --- | --- | --- |
| Empirical synthesis | [Steinmacher et al., IST 2015](https://doi.org/10.1016/j.infsof.2014.11.001) | Address orientation, documentation and technical barriers; no predicted AHL conversion effect |
| Empirical study | [Fronchetti et al., FSE 2023](https://2023.esec-fse.org/details/fse-2023-research-papers/47/Do-CONTRIBUTING-files-provide-information-about-OSS-newcomers-onboarding-barriers-) | Explain choosing tasks and communicating; repository-specific benefit still requires feedback |
| Community practice | [CHAOSS newcomer experience](https://chaoss.community/?p=4891) | Review repeat participation and qualitative obstacles, not only counts |
| Platform guidance | [GitHub contribution labels](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/encouraging-helpful-contributions-to-your-project-with-labels) | Label bounded starter work; visibility is not guaranteed adoption |
| Security practice | [OpenSSF Scorecard](https://openssf.org/projects/scorecard/) | Review supply-chain controls; no score, badge or security certification claimed |
| Context-dependent proposal | This plan | Offline activation before outreach; effectiveness remains an untested growth hypothesis |

## Audience and positioning

Primary audience: developers maintaining coding-agent instructions and project skills.
Secondary audience: evaluation engineers studying traces, regression and reproducibility.
Positioning: Improve coding-agent harnesses with evidence, not guesswork.
Supporting description: diagnose behavior, evaluate changes against baselines and review
evidence before adoption. Do not advertise general success gains or autonomous certification.

| Adjacent approach | Verified scope from primary source | AHL relationship and limits |
| --- | --- | --- |
| Manual instruction editing | AHL's owned AGENTS.md/CLAUDE.md staging surfaces | AHL adds collection, diagnosis and comparison; better results are not established |
| DSPy | [ICLR 2024 paper](https://proceedings.iclr.cc/paper_files/paper/2024/hash/f1cf02ce09757f57c3b93c0db83181e0-Abstract-Conference.html): modular LM pipelines and metric-directed compilation | Overlap in controlled optimization; AHL focuses on project harness evidence and native runtime workflows; no superiority claim |
| SWE-bench | [ICLR 2024 paper](https://proceedings.iclr.cc/paper_files/paper/2024/hash/edac78c3e300629acfe6cbe9ca88fb84-Abstract-Conference.html): issue-solving evaluation | AHL's public synthetic pack is a control fixture, not equivalent benchmark coverage |
| Phoenix | [Source README](https://github.com/Arize-ai/phoenix): tracing, evaluation, datasets and experiment tracking | Overlap in evidence inspection; AHL is a local harness toolkit. Product documentation confirms scope, not independent comparative quality |
| OpenHands | [Source README](https://github.com/OpenHands/OpenHands): agent frontend/orchestration and related SDK/workspace components | Adjacent agent execution infrastructure; AHL is not a replacement platform |

This comparison is intentionally narrow. No competitor was installed or benchmarked.
Commercial effectiveness claims were not used as growth evidence.

## Work packages and dependencies

| ID / priority | Deliverable | Funnel stage | Delivery / next action |
| --- | --- | --- | --- |
| GROW-01 / P0 | Clear README and preserved workflow reference | Understanding | [PR #26](https://github.com/mihazs/agent-harness-lab/pull/26); review exact-head CI |
| GROW-02 / P0 | Zero-credential command and troubleshooting | First experiment | PR #26; collect independent reproduction after merge |
| GROW-03 / P0 | Actual output and negative controls | Meaningful result | PR #26; optional recording deferred |
| GROW-04 / P0 | Contributor guide, templates, five issues | Contribution | [PR #32](https://github.com/mihazs/agent-harness-lab/pull/32), [#27](https://github.com/mihazs/agent-harness-lab/issues/27), [#28](https://github.com/mihazs/agent-harness-lab/issues/28), [#29](https://github.com/mihazs/agent-harness-lab/issues/29), [#30](https://github.com/mihazs/agent-harness-lab/issues/30), [#31](https://github.com/mihazs/agent-harness-lab/issues/31) |
| GROW-05 / P1 | Navigation and focused tutorials | Repeated use | PR #26; #29/#30 remain contributor work |
| GROW-06 / P1 | Metadata and optional Discussions | Discovery / feedback | BLOCKED: administration mutation/browser unavailable; manual steps below |
| GROW-07 / P1 | Reproducible public diagnostic case study | Trust | PR #26; real-agent evidence remains under scientific roadmap |
| GROW-08 / P2 | Articles, channel review and launch draft | Discovery / feedback | [Launch drafts](launch-drafts.md); maintainer approval and rule checks before posting |
| GROW-09 / P2 | Definitions, feedback experiments and review cadence | Activation / retention | Plan below; collection has not begun |

Starter issues have detailed implementation/test plans and good-first-issue/help-wanted
labels. Leave them available for external participation; they are not completed features.
Do not duplicate or close scientific issues to satisfy this roadmap.

## Manual metadata and community actions

No administration setting was changed. In the repository About panel, review the
following copy, then set only accurate fields:

- Description: Evidence-driven coding-agent harness diagnosis and baseline/candidate evaluation.
- Topics: coding-agents, ai-agents, agent-harness, claude-code, codex, agent-evaluation,
  developer-tools, python, llm-evaluation.
- Homepage: after the docs merge, use the repository's docs directory URL:
  https://github.com/mihazs/agent-harness-lab/tree/main/docs . A separate website is unnecessary now.
- Social preview: prepare a restrained graphic reading “Agent Harness Lab” and
  “Evidence-driven harness analysis and evaluation.” A preview image has not been generated
  or uploaded. Follow [GitHub's current format requirements](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview).
- If moderation capacity exists, Settings → General → Features → Discussions.
  Create General, Questions, Ideas, Show and Tell, Research and Evaluation, Announcements.
  Adapt available category formats; do not promise a format the account cannot select.
  Post the welcome draft only after activation is confirmed.
- Inspect Security settings for private vulnerability reporting and available supply-chain
  controls. Presence of SECURITY.md and Dependabot does not establish enabled private reporting,
  secret scanning, code scanning or branch protection.

Re-read GitHub state afterwards and update SUPPORT.md only when Discussions really exists.
Do not weaken branch protections or add certification badges.

## Measurement without trace collection

| Stage | Metric / definition | Collection and limits |
| --- | --- | --- |
| Discovery | Stars/forks; GitHub visitors/referrers | Snapshot dates and windows; traffic is access/retention limited and was unavailable here |
| Activation | Voluntary reports of a completed exact-commit quickstart | Record OS/Python, steps and obstacles, with consent; unknown denominator means no conversion rate |
| Useful result | User can explain what the finding does and does not prove | Ask after reproduction; one synthetic result is not recurring adoption |
| Community | First external PR, merged external PRs, return contribution within a stated window | Exclude maintainer and bots; review content and permissions before retaining identifiers |
| Responsiveness | Time from an external issue/PR to first substantive human response | Report median and sample count; bot replies are excluded; no SLA promised |
| Completion | PR time to merge/close and issue resolution with outcome | Closed is not necessarily solved; retain disposition |
| Sustainability | Releases, test failures, unresolved support and maintainer time | Manual bounded review; do not game volume or frequency |

Initial adoption and external-contributor counts are unknown, not inferred from stars.
Clones are not installations, release downloads are not active users, and issue/PR
volume is not quality. No analytics SDK, remote trace collection or background task is added.

Proposed small experiments: ask willing developers to explain the opening README, then
attempt the exact-commit quickstart and interpret the finding. Note which step fails and why.
Compare observed obstacles before/after a focused doc change without presenting a small
convenience sample as a causal or statistically powered study. Ask permission before recording
feedback. Prefer qualitative fixes to an underpowered A/B conversion experiment.

## 30/60/90-day milestones

Time windows begin when the maintainer adopts the plan; no appointments are scheduled.

| Window / priority | Owner and deliverables | Dependencies / evidence | Risks and decision rule |
| --- | --- | --- | --- |
| Days 1–7 / P0 | Maintainer: review/merge #26 then #32; retain five accessible issues; review metadata | Passing exact-head checks; reproduce quickstart; verify merged state and settings | CI queue or setup failures: fix before outreach, do not bypass checks |
| Days 8–21 / P1 | Maintainer with volunteers: try onboarding, improve tutorials, decide Discussions capacity | Voluntary synthetic-only reproduction notes; issue scopes and review instructions | No willing testers: report missing feedback, avoid invented adoption |
| Days 22–30 / P1–P2 | Maintainer: review three articles and demonstration; seek independent feedback if authorized | Working public source path; accurate docs and limitations; current channel rules | If try-out or claims fail review, postpone launch |
| Days 31–45 / P2 | Maintainer: publish selected educational material and optionally Show HN after approval | Maintainer can explain personal motivation and attend feedback; no coordinated voting | Channel ineligibility or unanswered support: reduce distribution scope |
| Days 46–60 / P1 | Maintainer/contributors: fix observed obstacles, acknowledge reviewed contributions, follow up with willing participants | Actual issues/PRs and opt-in usage reports; scientific dependencies preserved | No external PR: inspect barriers rather than manufacture contributions |
| Days 61–90 / P2 | Maintainer: repeat bounded health review, publish an evidence-backed follow-up only if warranted | Compare consistent measurement windows and sample counts; include failures | High support workload: simplify scope and defer optional channels |

30-day objective hypotheses: public reproducible quickstart, one controlled demonstration,
five accessible issues and independent onboarding feedback if volunteers participate.
60-day objective hypotheses: one useful external contribution and a documented response to
feedback. 90-day objective: evidence of repeat use/contribution and a sustainable review process.
These are desired observations, not forecasts or commitments; there is no star quota.

Suggested maintainer cadence: a short weekly triage and monthly measurement review if capacity
permits. Scope incoming work, acknowledge questions, explain acceptance criteria, and record
blocked/deferred outcomes. No automation was created.

## Continuation and validation handoff

Repository: https://github.com/mihazs/agent-harness-lab . Baseline main:
`a2a3d3abb71c14ea4f384d8ef1cd4015f94e6104`.
PR #26 head: `1c5fdd7b8ce9db0eefda6fdbf60a2cefaccd2008`.
PR #32 head: `fe0cf733892f6b03fd73be63f8a2a36f86aff59b`.
Later updates must re-read heads rather than assume these remain current.

The execution host disconnected after local validation. PR #26 contains README,
docs index/install/getting-started/workflows, synthetic input/report and
tools/tests/test_quickstart.py. PR #32 contains CONTRIBUTING and issue/PR templates.
No direct Codex invocation capability was found; this is a prepared handoff.

Next executor: read AGENTS.md and open PRs, fetch exact remote heads, inspect diffs and
CI runs, preserve concurrent work, then run both README unittest suites and
`python3 -m unittest discover -s tools/tests -p 'test_quickstart.py'`.
Reproduce the documented diagnosis into a fresh temporary path and compare the full
report. Check relative links, fences, shell syntax, privacy and rendered Markdown.
Review the later strategy/draft documentation separately; do not assume it was shell-tested.
Do not alter graders, installer protections, version numbers, evaluation gates or private files.
No paid inference, uploads of private traces, external promotion or automatic schedule.

Acceptance: exact documented output, truthful limitations, passing required checks at each PR
head, no accidental private data, focused diffs and confirmed GitHub merge state.
Correct findings, rerun affected checks and merge #26 before #32 only if repository policies
permit. A queued or unrelated green run is not a pass. Keep starter issues open for volunteers.
Record commit SHAs, commands, actual results, skips, CI URLs and blockers in #25.
