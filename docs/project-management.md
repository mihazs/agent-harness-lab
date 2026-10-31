# GitHub Project operations — Agent Harness Lab

The live execution board is [Agent Harness Lab — Roadmap](https://github.com/users/mihazs/projects/3)
(owner `mihazs`, Project **3**, node ID `PVT_kwHOADx7kc4Bpo2X`).
It is linked to [mihazs/agent-harness-lab](https://github.com/mihazs/agent-harness-lab).
The Project is private; repository issues and this guide remain accessible under
their existing permissions. Visibility was not expanded.

## Configured fields and classification

| Field | Type | Options / source |
| --- | --- | --- |
| Status | Single select | Backlog, Ready, In Progress, In Review, Blocked, Done |
| Priority | Single select | P0, P1, P2, P3; mirrors `priority: P0`–`priority: P3` |
| Workstream | Single select | Scientific Reliability, Data Sources, Product & DX, Community & Growth |
| Roadmap | Single select | Now, Next, Later; mirrors `roadmap: now/next/later` |
| Effort | Single select | XS, S, M, L, XL; currently unset |
| Review & CI | Text | Current-head PR review decision, aggregate CI state and merge state |

Workstream mirrors `track: science`, `track: data sources`, `track: product`,
and `track: community`, respectively. Labels are authoritative; do not classify
work by editing these three mirrored fields alone.

Existing native Title, Assignees, Labels, Linked pull requests, Parent issue,
Sub-issues progress, Milestone, Repository, Reviewers, Created, Updated and Closed
fields are reused. There are no duplicate classification fields. No deadlines,
assignments, estimates or milestones were invented. Iterations and optional
start/target dates were omitted because no cadence or actual dates were established.

Implementation tasks retain one priority, track and roadmap label. Epics retain
their track and `type: epic` without invented task priorities. Historical PRs
without classification labels remain unclassified. Preserve `good first issue`,
`help wanted`, `documentation`, `enhancement`, and existing workflow labels.

### Field and lifecycle synchronization

Native Project workflows do not mirror arbitrary repository labels into custom
fields. Use the foreground, standard-library synchronizer during maintainer triage:

```sh
gh auth status
python3 scripts/sync_project.py          # preview
python3 scripts/sync_project.py --apply  # apply mirrors, lifecycle status and PR snapshots
python3 scripts/sync_project.py          # expect zero proposed changes
```

The script uses the authenticated GitHub CLI with `project` and repository access.
It verifies the repository link, paginates Project items, skips other repositories
and drafts, rejects conflicting labels and truncated label/field responses, and
resolves actual field/option/item IDs. Removing a classification label clears its
mirror. Lifecycle reconciliation sets completed issues and merged PRs to Done,
closed non-completed issues and unmerged PRs to Backlog, reopened issues previously
marked Done to Backlog, and open non-draft PRs to In Review unless manually Blocked.
Draft PRs retain manual implementation status, but do not remain In Review/Done.
Manual Ready/In Progress/Blocked issue states are preserved. It does not change
labels, ownership, dates, estimates, issue state, PR state, workflows or repository settings. Review & CI is a snapshot;
rerun after PR head/check/review updates and follow GitHub's live checks before
merging. It distinguishes drafts, missing reviews, requested changes, approval,
pending/failing/passing CI and the actual merge state without treating approval
as passing CI. Rerun after concurrent label changes; there is no atomic
cross-resource transaction or automatic schedule.

## Status policy

| Status | Evidence required |
| --- | --- |
| Backlog | Accepted work without a verified ready implementation slice; also reopened work awaiting triage |
| Ready | A useful specified slice can proceed with satisfied prerequisites |
| In Progress | Actual ongoing implementation supported by current evidence |
| In Review | An actual open PR or linked implementation is awaiting review, checks or merge |
| Blocked | A confirmed unmet prerequisite prevents the whole next useful slice |
| Done | Issue completed or PR merged; this is delivery state, not measured model effectiveness |

Do not mark an issue In Progress because it has a detailed specification.
A dependency can gate certification while offline interfaces or fixtures remain
useful work. A native blocked-by link therefore does not automatically imply
Blocked Status. Synthetic tests alone do not close live integration,
independent grading or research acceptance criteria.

Initial verified inventory: **43 issues and 16 PRs**, all 59 represented once.
All historical PRs were included, not just recently merged ones. Verified Status
totals after the concurrent merge of PR #19: 27 Backlog, 10 Ready, 1 In Review,
21 Done. Its existing merge process was not changed by Project setup. No issue acceptance criteria
were rewritten or issues closed as part of Project organization.

## Saved views

All ten views are saved in Project 3. Common displayed fields include title,
Status, priority, workstream, roadmap, assignee, labels, linked PRs and parent.

| View | Layout / organization | Saved filter |
| --- | --- | --- |
| Execution Board | Board; columns in Status option order | `is:open` |
| Roadmap | Table grouped by Workstream; no fabricated timeline | `is:open` |
| Scientific Reliability | Table; prerequisite order, including epic #3 and completed foundations | `label:"track: science"` |
| Data Sources | Table grouped by Roadmap; source foundations before dependent adapters | `label:"track: data sources"` |
| Product & Developer Experience | Table; onboarding, viewer, interop and campaigns | `label:"track: product"` |
| Community & Growth | Table; epic #25 and contributor work, preserving historical delivery | `label:"track: community"` |
| Immediate Priorities | Table grouped by Priority; Priority ascending, then Status descending (Ready ahead of Backlog) | `is:open label:"roadmap: now"` |
| Contributor Opportunities | Table; Status and linked PRs visible | `is:issue is:open label:"good first issue","help wanted"` |
| Review & Release | Table; Review & CI snapshot visible alongside actual PR links | `is:pr is:open` |
| Completed | Table; historical closed issues and PRs | `is:closed` |

The Execution Board's open-only filter keeps completed cards out of active work;
its Done column is intentionally empty. The Completed view retains historical
work. A closed, unmerged PR is historical, not completed delivery: inspect its
native state and Status rather than interpreting the view name as acceptance.
Review & Release includes the release PR and all open PRs, including unlabeled PRs.

## Epics and dependencies

Native parent/sub-issue relationships preserve existing epic descriptions:

- Scientific Reliability **#3**: #4–#17 (14 children).
- Community & Growth **#25**: #27–#31 (5 children).
- Data Source Compatibility **#41**: #42–#58 (17 children).

There are 54 native blocked-by links from explicit prerequisites in issue
descriptions. The scientific chain starts with completed provenance #6, isolation
#7, task bank #8 and paired evaluation #9; routing #10 precedes skills/adapters
and candidate optimization, followed by retrieval, monitoring, benchmarking and
optional research. Conditional integrations are not made unconditional blockers.

Data adapters with explicit source-contract prerequisites depend on #42.
The existing native-session extension #44 retains its independent scope. The
portable parser #47 precedes full OKF integration in #43, but #43's minimal
read-only export/MCP slice need not wait for that optional full integration.
#58 links to #42; its relevant importer requirements remain slice-specific rather
than forcing every possible provider to ship first. No cyclic dependencies were
found. Native dependency details are available on the linked issues; the Project
shows parents and linked PRs without duplicating epic content.

Early ready slices include #7/#8/#9/#10, independent onboarding #36, report and
interop contracts #37/#38, source architecture #42 and contributor issues #27/#29.
Ready denotes an available bounded slice, not full certification readiness or an
assignment. #43–#47 and #58 retain their label priorities and roadmap phases.

## Native workflows

| Workflow | Actual configuration |
| --- | --- |
| Auto-add to project | Repository `mihazs/agent-harness-lab`; filter `is:open`; new or updated issues and PRs |
| Auto-add sub-issues | Add native sub-issues when a parent is in the Project |
| Item added to project | Issues and PRs → Backlog |
| Item closed | Disabled: native closure trigger cannot distinguish completed from not planned |
| Pull request merged | PR → Done |
| Item reopened | Issues and PRs → Backlog for evidence-based retriage |
| Pull request linked to issue | Linked issue → In Review |

**Disabled:** Item closed, Auto-close issue, Auto-archive items, Code changes requested,
and Code review approved. Board movement cannot close issues. No auto-merge
setting, CI definition, branch protection, deployment, paid evaluation or
background job was enabled or changed.

The native close workflow has no completion-reason selector, so it is disabled.
Run the synchronizer after closures: only stateReason COMPLETED becomes Done;
NOT_PLANNED or missing reasons remain Backlog. Unmerged closed PRs also remain
Backlog. Reopened PRs initially return to Backlog through the native workflow;
the synchronizer restores non-draft PRs to In Review while respecting manual
Blocked status. Native workflows cannot calculate dependency readiness.
Readiness remains a maintainer decision because a prerequisite may gate full
certification while an independent useful slice can proceed. No heuristic
dependency-to-Status automation is installed.

Workflow enabled states and trigger/action settings were read back through the
API and authenticated UI. The existing merge of PR #19 was observed during setup and its Project item
automatically reached Done. No artificial issue closure/reopening or merge was
performed to test event delivery. Label sync was executed against the real
Project and a second run proposed zero changes. Other event delivery remains subject
to GitHub processing; verify the first natural add/reopen event. Synthetic
lifecycle tests cover completion reasons, reopen, draft and unmerged PR states,
plus pagination and rejection of conflicting labels before any write.

## Maintainer procedure and limitations

1. Check open PRs before starting work; use linked PRs and Review & Release to
   avoid duplicate contributions.
2. Apply authoritative classification labels, then run the synchronizer in
   preview/apply/preview sequence, including after closure or reopening. Resolve
   conflicts before applying.
3. Verify prerequisites against the issue's current scope. Move a bounded slice
   to Ready; move to In Progress only with actual implementation evidence.
4. Inspect current-head checks, reviews and unresolved discussions before merge.
   Queued, cancelled, skipped or unknown checks are not passing evidence.
5. Close only completed acceptance scope. Keep #7–#17 and the scientific epic
   open while their independent integration/evaluation requirements remain.
6. Backfill after imports or interrupted auto-add processing; compare paginated
   repository issue/PR inventories to Project content IDs and check duplicates.
7. Preserve completed cards rather than auto-archiving them. Review roadmap
   phases and stale review snapshots during normal maintainer triage.
8. Review dependencies for cycles after edits, and preserve unrelated work.
   Save view changes explicitly and read them back; a changed browser URL alone
   does not update the saved configuration.

The Project is an execution index, not an effectiveness oracle. Missing capture,
unknown lineage, untested isolation and inconclusive evaluations stay explicit.
Never put private session bodies, memory-derived information, analyzed-project
identities, credentials, private findings or private-derived aggregates in public
issues, documentation, reports or Project fields.

Documentation changes are maintained in PR #59 and require its normal review/CI;
the Project exists independently of that PR being merged.

References: [GitHub Projects quickstart](https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/quickstart-for-projects),
[Project filtering](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/filtering-projects),
[Auto-add/backfill](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/adding-items-automatically).
