# Why reuse is split by authority

[Reuse contract matrix](memory-reuse-contract-matrix.md) · [Controller history](oracle-history.md)

Agent Harness Lab already keeps several authoritative records: source lineage, a sealed
evaluation history with oracle quarantine, frozen plans, preservation cohorts and
one-shot diagnostic access. A persistent evidence layer must not become a second
authority over any of them. This page explains how the matrix keeps three things apart.

## Computation, history and evidence

Cached **computation** can replace redundant work when its inputs are identical. A
deterministic stage such as event analysis is a function of normalized events and an
algorithm, so matching identities justify reuse. A parser or algorithm change misses
only the stages that depend on it, which is what the fixtures `changed_parser` and
`changed_algorithm` assert.

Past **outcomes** inform longitudinal understanding but are never fresh trials. A sealed
comparison was valid under one grader, bank, plan, runtime and configuration. When any
of them differs, the honest label is `not_comparable`, not "no regression". When the
oracle is quarantined the record is `blocked`, because the owning store says so.

A new behavioral claim needs **fresh qualified evidence**: complete scheduled arms,
frozen plan, sealed controller run. Old results cannot be relabeled as independent
trials, and an interrupted run is never resumed. Reuse therefore stops at the sealed
comparison: the matrix marks `fresh_trial` as `not_reusable` in every fixture.

## Authority is read live

Quarantine, bank authority and decision receipts are designed so a stored label is not
proof: `verify_decision` recomputes from live authority, and `checked` re-reads
quarantine on every call. A cache of those answers could only go stale in the unsafe
direction, so the matrix sets the decision to `verify_then_replay`: the owner validates
first, and a cached value is only a hint.

## Unknown stays unknown

Source adapters can detect that content changed but cannot show how recent it is, and
the repository has no secret scanner. The matrix therefore reports remote freshness as
`unknown`, and treats a trace that may contain a secret as blocked for persistence
rather than assuming derived output is clean. These are findings about today's code, not
design preferences; a later slice can replace them once an adapter or scanner proves more.

## Why the recommended defaults are conservative

Local, explicit opt-in avoids guessing a storage location and keeps the store outside
project, bank, oracle, credential and trial directories. Derived summaries instead of raw
bodies keep private content out of any index. Excluding historical findings from role
projections preserves the existing role isolation. Treating the knowledge wiki as a
navigation layer follows from all of the above: it can point to evidence but cannot
decide what the evidence means.

## A harness has an identity and a history

A comparison is only as precise as the statement of what changed and what it was compared
against. The [snapshot id](harness-snapshots.md) therefore names the declared surface
files, their modes and the declared runtime and trust, and not a commit: a commit says
nothing about uncommitted or ignored files, so two snapshots of one commit can differ and
the commit relation is a separate annotation. Timeline events point at the authoritative
record and never carry a cause, because a change that precedes a result is not shown to
have produced it. A baseline chosen after outcomes were seen is a different, descriptive
claim, so a fixed reference must be declared first, and reselecting one is recorded and
never lifts an existing claim.
