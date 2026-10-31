# GitHub Project operations — Agent Harness Lab

This document defines the recommended GitHub Projects (v2) structure for
[mihazs/agent-harness-lab](https://github.com/mihazs/agent-harness-lab).
GitHub issues and PRs remain authoritative; a project card or CI success is
not evidence of improved coding-agent outcomes.

**Current scope:** repository issue/PR labels have been applied; this
document does not itself create a Projects board, populate items, set fields,
create views or enable workflows. Confirm the existing project before making
project-level changes. Do not duplicate an already linked project.

## Applied issue-label taxonomy

Labels were added without deleting existing contributor labels.

| Axis | Values | Meaning |
| --- | --- | --- |
| Priority | `priority: P0`, `priority: P1`, `priority: P2`, `priority: P3` | P0 is a correctness/evidence prerequisite; P3 is optional research |
| Track | `track: science` | Controlled evaluation, safety, algorithms |
| Track | `track: data sources` | Native and third-party evidence ingestion |
| Track | `track: product` | CLI, reporting, interoperability, campaigns |
| Track | `track: community` | Documentation, onboarding, contributions |
| Roadmap | `roadmap: now`, `roadmap: next`, `roadmap: later` | Relative sequence, **not** deadlines |
| Type | `type: epic` | Coordination parent, not directly executable |
| Workflow | `workflow: in-review`, `workflow: release-pending` | Linked PR review or release coordination |

Maintain exactly one priority, one track and one roadmap label per open
implementation task. Epics have a track and type but intentionally no
implementation priority/roadmap. Keep `good first issue`, `help wanted`,
`documentation` and `enhancement` labels intact.

The GitHub Projects `Status` field should describe actual activity; priority,
track and roadmap already exist as issue labels and should **not** be duplicated
into unsynchronized custom fields unless a future automation synchronizes them.

## Existing portfolio

| Track | Epic | Execution notes |
| --- | --- | --- |
| Science | [#3](https://github.com/mihazs/agent-harness-lab/issues/3) | Start with role isolation #7, task bank #8 and paired runner #9; continue #10–#16; #17 is optional research |
| Data sources | [#41](https://github.com/mihazs/agent-harness-lab/issues/41) | #42 source contract first; #44–#47 may progress on scoped primitives; #43 requires #42/OKF; #48–#53 are adapters/evidence; #58 handles correlations; #54–#57 expand providers |
| Product | Issues #36–#39 | #36 onboarding works independently; #37 viewer consumes evaluated evidence; #38 interop preserves provenance; #39 campaigns depend on verified execution and budgets |
| Community | [#25](https://github.com/mihazs/agent-harness-lab/issues/25) | #27–#31 contain bounded contributor work. Check linked PRs before assigning, closing or duplicating |

A `roadmap: now` label means **some** bounded work can begin; it does not
claim every downstream integration is unblocked or that somebody is assigned.
Do not conflate synthetic test completion with independent real-model certification.

## Recommended existing-or-new GitHub Project

- **Name:** Agent Harness Lab — Roadmap
- **Description:** Evidence-driven science, source compatibility, DX, and community delivery.
- **Repository:** only `mihazs/agent-harness-lab` unless a maintainer deliberately expands scope.
- **Visibility:** review existing project permissions and the sensitivity of future
  notes before choosing public; do not copy private project/session traces, memory,
  derived findings or private aggregates into public fields or charts.

### Status column policy

Use the native Project Status field with the following options, when configurable.

| Status | Enter when |
| --- | --- |
| Todo | Item is in the accepted backlog, not currently being executed |
| Ready | A PR-sized slice is specified, required prerequisites can be met |
| In progress | Actual implementation has begun |
| In review | A real linked PR is open and awaiting review/CI/merge |
| Blocked | A concrete unmet dependency/permission/runtime capability prevents the next slice |
| Done | Issue closed or linked PR merged with relevant checks; never infer real-world improvement |

Enable built-in **Item added -> Todo**, **issue closed -> Done**, and
**PR merged -> Done** workflows. Avoid automatically closing GitHub issues from
a card movement, enabling auto-merge, or invoking paid evaluation. Mark epics
Done only after their tracked requirements are actually complete.

### Saved views (create in the Project UI)

These filters use GitHub Projects syntax. Quote labels containing spaces.

| View | Layout/grouping | Filter |
| --- | --- | --- |
| Execution | Board; columns by Status | `is:open` |
| Now | Table; sort/filter by priority label | `is:issue is:open label:"roadmap: now"` |
| P0 gates | Table | `is:issue is:open label:"priority: P0"` |
| Science | Table | `is:issue is:open label:"track: science"` |
| Data Sources | Table | `is:issue is:open label:"track: data sources"` |
| Product | Table | `is:issue is:open label:"track: product"` |
| Community | Table | `is:issue is:open label:"track: community"` |
| First contributions | Table | `is:issue is:open label:"good first issue"` |
| Review & release | Table | `is:pr is:open` |
| Completed | Table | `is:closed` |

Set title, labels, status, assignee and linked PR as visible where supported.
Do not assign people, estimates, dates, milestones or iterations without a
real decision. A new task should first have evidence, owner (when available),
acceptance criteria, a link to its epic and a small tested delivery slice.

## Project backfill and future automation

GitHub's built-in auto-add only processes **new or updated** items; it does not
retroactively add every currently matching issue. Select and verify the actual
Project number first. GitHub CLI project operations require an authorized
`project` scope; authorize using GitHub's own authentication flow, never by
pasting tokens in issues or chats.

```sh
gh auth status
gh project list --owner mihazs
gh project view PROJECT_NUMBER --owner mihazs
```

After verifying ownership and existing contents, a maintainer can link and
backfill the correct Project:

```sh
export PROJECT_NUMBER=YOUR_VERIFIED_PROJECT_NUMBER
gh project link "$PROJECT_NUMBER" --owner mihazs --repo mihazs/agent-harness-lab

gh issue list --repo mihazs/agent-harness-lab --state all --limit 200 \
  --json url --jq '.[].url' |
  while IFS= read -r item_url; do
    gh project item-add "$PROJECT_NUMBER" --owner mihazs --url "$item_url" || exit 1
  done

gh pr list --repo mihazs/agent-harness-lab --state open --limit 200 \
  --json url --jq '.[].url' |
  while IFS= read -r item_url; do
    gh project item-add "$PROJECT_NUMBER" --owner mihazs --url "$item_url" || exit 1
  done
```

GitHub returns the existing Project item when the same issue/PR has already
been added. Review the count and failures before proceeding. Then in
**Project -> Workflows**, configure **Auto-add to project** for this repository
using an appropriate issue/PR filter, enable **Item added -> Todo** and the
close/merge workflows. Do not auto-add unrelated repositories or create a
second project unnecessarily.

If there is no suitable existing project, create one through the authorized
GitHub UI or GitHub CLI, then follow the same steps. The issue connector alone
cannot verify/create Projects views or set their custom fields.

## Maintenance rules

- Look up current issue/PR state and exact-head checks before editing Status;
  queued, failed or skipped checks are not passing evidence.
- Preserve the distinction between proposed change, validated synthetic API,
  independently graded effect and actual release/adoption.
- Link dependencies to the authoritative issues rather than duplicating backlog.
- Do not publish private traces, project identities, user credentials, session
  contents, memory data or private-derived statistics in the board.
- Review `roadmap: now` periodically; promote tasks only after prerequisites
  and implementation capacity are available.
- Never claim a Project view or automation was created solely because its
  configuration is documented here.

References: [GitHub Projects quickstart](https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/quickstart-for-projects),
[Project filtering](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/filtering-projects),
[Auto-add/backfill limits](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/adding-items-automatically),
[GitHub CLI project commands](https://cli.github.com/manual/gh_project).
