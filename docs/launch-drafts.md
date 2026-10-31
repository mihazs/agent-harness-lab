# Launch materials — drafts for maintainer review

Prepared 2026-10-31. Nothing in this file has been submitted externally.
These are short English drafts, not evidence of adoption. Publication requires
a working merged quickstart, current rule checks and maintainer approval.

## Article 1: Why coding-agent harness changes need baseline evaluation

A project instruction can look clearer without changing an agent's results. A shorter
trace can also omit required work. Before accepting a harness edit, define the behavior
that must improve and the behavior that must remain intact.

A practical comparison starts with a frozen task, baseline instructions and a candidate.
Keep runtime settings, tools, permissions and resource budgets comparable. Grade the
resulting artifacts against explicit requirements, and test the grader against broken,
partial and valid solutions. A grader that accepts everything cannot support a repair claim.

Software-agent evaluation is an existing research problem. SWE-bench evaluates edits
against real repository issues [1]. DSPy studies metric-directed optimization of language
model pipelines [2]. Neither paper establishes that a particular AHL instruction edit works.

In AHL, a diagnostic observation is the start of an investigation. Repetition may be
necessary; a successful process exit may hide an incorrect artifact. Development feedback
can inform the next candidate, but a task used for that feedback is no longer untouched
certification evidence. Repeated matched trials and independent task review remain necessary
parts of the scientific roadmap.

A useful report therefore includes failures, unknown outcomes, trial conditions and untested
boundaries. AHL's synthetic examples verify specific implementation behavior. They do not
establish general coding-agent gains, and automatic behavioral adoption remains blocked.

Sources: [1] [SWE-bench, ICLR 2024](https://proceedings.iclr.cc/paper_files/paper/2024/hash/edac78c3e300629acfe6cbe9ca88fb84-Abstract-Conference.html).
[2] [DSPy, ICLR 2024](https://proceedings.iclr.cc/paper_files/paper/2024/hash/f1cf02ce09757f57c3b93c0db83181e0-Abstract-Conference.html).
Project contract: [evaluation foundation](evaluation-foundation.md).

## Article 2: Diagnosing coding-agent behavior with Agent Harness Lab

Before changing instructions, inspect what the trace actually records. AHL includes a
four-event synthetic example: two read-only call/result pairs with matching tool, input,
result and recorded state identifiers. No agent login or model call is needed.

From a public source checkout with Python 3.11+, run:

```sh
python3 -m agent_harness_lab diagnose --events examples/repeated-observation.jsonl --output /tmp/ahl-article-diagnosis
cat /tmp/ahl-article-diagnosis/report.md
```

Choose a new output directory on every attempt. The command writes its report rather
than printing a summary. The reproduced report contains:

```text
Events: 4; cases: 1.
Observed findings: 1.
Recurring sequences: 0.
```

The finding is a duplicate observation at events 2 and 4. Its status is observed_pattern.
That label matters: the trace does not establish whether the second read was required.
It also supplies no verified outcome or independent lineage.

The committed controls change the result, change state or remove a required fingerprint.
Each suppresses the finding. Those checks help explain the detector's boundary; they
are not a candidate agent's performance result. The full report is checked into the
repository so a reader can compare their run.

If the destination exists, select a fresh path. If your release installation lacks
the example, use the source checkout. Missing evidence should remain unknown rather
than be converted into a successful task. SWE-bench's artifact-oriented evaluation
setting [1] motivates examining results beyond a plausible narrative; this tiny
diagnostic example is not equivalent benchmark evidence.

Reproduction: [first diagnosis](getting-started.md), [actual full report](../examples/repeated-observation-report.md).
Primary scientific context: [1] [SWE-bench, ICLR 2024](https://proceedings.iclr.cc/paper_files/paper/2024/hash/edac78c3e300629acfe6cbe9ca88fb84-Abstract-Conference.html).

## Article 3: What we can and cannot prove about autonomous harness improvement

A test can verify a matching rule without proving an autonomous system improves.
The first claim concerns a specified input and implementation. The second requires
evidence about tasks, outcomes, evaluation validity and the conditions under which
results transfer.

AHL's public case study establishes that its explicit-input diagnosis produces one
expected observation and that several negative controls suppress it. It establishes
no model success gain, cost saving or real-world diagnostic accuracy.

For a harness experiment, a candidate must not shape the independent grader or
consume certification tasks as training feedback. Testing broken, partial and valid
solutions can reveal grader defects, but passing those controls is not semantic proof.
Repeated trials address variation only to the extent that tasks, runtime conditions
and dependence are understood. Shared task roots cannot be counted as independent evidence.

SWE-bench [1] provides a concrete task-oriented evaluation setting; DSPy [2] illustrates
optimization against a metric. AHL draws on the need for explicit tasks and measurable
objectives without importing those projects' results as its own.

A separate limit concerns security. Fresh workspaces and cooperative graders are not
a hostile-code sandbox. Unknown isolation, mutable runtime settings or missing lineage
must remain visible and can block certification. AHL consequently keeps behavioral
adoption recommendation-only while safeguards and independent evidence remain incomplete.

The useful next step is a narrower, falsifiable experiment with frozen inputs, reviewed
outcomes and explicit resource limits. Publishing an inconclusive result is preferable
to presenting a synthetic smoke check as general autonomous improvement.

Sources: [1] [SWE-bench, ICLR 2024](https://proceedings.iclr.cc/paper_files/paper/2024/hash/edac78c3e300629acfe6cbe9ca88fb84-Abstract-Conference.html).
[2] [DSPy, ICLR 2024](https://proceedings.iclr.cc/paper_files/paper/2024/hash/f1cf02ce09757f57c3b93c0db83181e0-Abstract-Conference.html).
Project boundaries: [scientific roadmap](https://github.com/mihazs/agent-harness-lab/issues/3),
[evaluation contract](evaluation-foundation.md), [controlled experiments](controlled-experiments.md).

## Show HN preparation

[Official guidelines](https://news.ycombinator.com/showhn.html) were read on 2026-10-31.
They call for something readers can try, easy access, personal work and an author
available to discuss it. Do not solicit upvotes/comments or submit a reading-only page.
Eligibility is a maintainer judgment, not an endorsement from HN. Recheck rules at submission.

Suggested title:
Show HN: Agent Harness Lab – inspect coding-agent traces and compare harness changes

Suggested URL:
https://github.com/mihazs/agent-harness-lab

Suggested introductory comment:

Agent Harness Lab is an experimental Python toolkit for diagnosing coding-agent
traces and evaluating changes to project instructions and skills.

You can try a public synthetic diagnosis without credentials or inference. The
README links the command, full output and negative controls. Agent-backed workflows
support Claude Code and Codex, but broad performance gains are unproven and behavioral
adoption is blocked pending certification safeguards.

The small demonstration identifies repeated observations; it does not prove those
reads were wasted. I would value feedback on whether the output and its limitations
are understandable, and on what evidence would make a harness comparison useful.

Before publishing, the maintainer must add their own accurate motivation and confirm
availability to answer questions. No personal story or endorsement has been invented.

## Other channels and curated lists

| Candidate | Relevance | Rule/eligibility state | Action |
| --- | --- | --- | --- |
| r/opensource | Open-source usability and contribution discussion | Official rules endpoints could not be retrieved; third-party promotional summaries excluded | DEFERRED: read current rules/flairs in the community before drafting or posting |
| r/ClaudeAI | Potential relevance to supported runtime workflows | Official rules retrieval failed; permission to self-promote not established | DEFERRED: verify rules and appropriate thread first |
| hesreallyhim/awesome-claude-code | Potentially relevant to Claude Code skills/tooling | Current contribution rules and existing-entry check could not be completed reliably | DEFERRED: inspect CONTRIBUTING and search entries before preparing a submission |
| sindresorhus/awesome | Index of curated lists, not a general tool-directory slot | [Contribution template](https://github.com/sindresorhus/awesome/blob/main/pull_request_template.md) inspected; direct AHL fit not established | Do not submit AHL directly; locate and qualify a specific relevant list |

No community-specific promotional draft is approved where rules are unverified.
No curated-list PR, moderator contact, developer outreach or external post was made.
Avoid identical cross-posts, fabricated endorsements and bulk contact.

## Optional welcome discussion draft

Use only after Discussions is enabled and the maintainer approves moderation scope.

Welcome to Agent Harness Lab. This experimental project investigates coding-agent
harness behavior through diagnosis and baseline/candidate evaluation.

Start with the credential-free synthetic quickstart. Questions belong in Questions,
ideas in Ideas, public synthetic demonstrations in Show and Tell, and methodology
questions in Research and Evaluation. Reproducible bugs and scoped implementation
work belong in Issues. Use SECURITY.md for private vulnerability reporting.

Please share only independently synthetic or authorized public data. Private traces,
project identities and history-derived findings must stay private. A diagnostic
pattern is not proof of waste, and synthetic checks do not establish broad agent gains.

We welcome documentation fixes, focused tests and methodological criticism. Please
check existing work, explain what you observed and report uncertainty. Maintainer
response times are not guaranteed.

## Publication sequence and stop gates

1. Merge and independently reproduce onboarding before inviting wider use.
2. Review actual output and scientific limitations.
3. Confirm metadata and accessible contributor tasks.
4. Review these drafts, sources and personal motivation.
5. Select one eligible channel and obtain approval before posting.
6. Collect voluntary feedback, fix observed obstacles, then consider another channel.
7. Publish follow-up evidence only when an actual experiment or useful release exists.

Stop if the quickstart fails, a claim exceeds evidence, rules remain unknown or
support exceeds maintainer capacity. No release should be cut solely to advertise
these drafts, and no promotion should rely on an unfinished experimental capability.
