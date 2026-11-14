# Why findings are separate and abstain

This explains the design choices behind [`ahl instruction-adherence`](instruction-adherence.md).

## Two directions, one extraction

An agent can fail a rule the harness gave it, or an instruction the user typed. The causes
and the repairs differ, so the report keeps the `direction`, but the extraction is shared:
the same sentence shapes serve both, and restatement reuses the content-free message
fingerprint that the [analysis brief](analysis.md) counts as a weak-outcome signal. A second
extractor would drift from the first.

## Loaded is a different finding from ignored

A rule can only be ignored by an agent that had it. When the capture shows the instruction
file was never loaded, a prohibited call says something about loading, not about the agent,
so it is reported as `not_loaded`. When loading is truncated or unknown the call is only a
possible violation. The `turn` in a `loaded` entry matters for the same reason: a call made
before the rule arrived is not an ignored rule.

## Unknown stays unknown

A prohibited call is observed evidence even in a partial capture. The absence of a call is not:
the missing part may hold it, and a skipped required step needs the whole session. Even then it
is judged only after a state-changing call: a session that only answered or read may owe an
"always run X" rule nothing, so whether the rule applied is unknown. A session
whose capture state is unknown decides nothing, and a session with no declared lineage is
never counted as an independent root. Collapsing an unknown into a pass would make a harness
look better than the evidence allows, and collapsing it into a failure would invent work.

## A repeated instruction is weak evidence

A user may repeat an instruction because the agent ignored it, or because they changed their
mind, answered a question or pasted the same text twice. The detector excludes the cases it can
recognize (a supersession cue, a repeat straight after the agent's question) and never lets a
restatement override checkable evidence. What remains is a `possible_violation`. On the shipped
sample its precision is far below the threshold, so it is reported as an abstention rather than a
finding. The abstention is the honest outcome, not a defect to tune away.

## Why native sessions are partial and Codex rules abstain

The telemetry adapters never keep text, because analysis must not depend on private content. This
report needs the command and the prompt, so it reads the capture again and keeps only turns, in memory.
It reads no more than those adapters would accept: a verified release, the project's own directory, a user
session. A capture that cannot be attributed is counted by reason and, in an analysis, reported as
`not_run`; an empty result never stands for a clean one.

A native capture can be cut at the tail, can omit records the runtime did not write and has no verified
lineage, so it is partial with unknown lineage. That keeps a prohibited call a violation (it was seen) and
a skipped required step unknown (it may be in the part not seen). Claude Code records the memory file it
attached, which is the only proof that a rule reached the agent. The pinned Codex rollout records none, so
a harness-rule violation there is a possible violation and, with the shipped calibration, an abstention:
the report would rather say it cannot tell than blame an agent for a rule it may never have received.

## Why not the controller trajectory engine

The trajectory assertions use the same vocabulary (forbidden, required, sequence) and the same
three-valued results, and this detector follows them. It does not feed captured history into
that engine, because the engine accepts only controller-issued live captures: a record that a
person assembled from a past session must never be given controller authority. Mapping a
checkable rule to a controller policy for a later controlled trial remains possible because the
rule kinds line up.

## Why the calibration is synthetic

Findings need a stated precision before anyone relies on them, and real sessions are private, so
the sample is authored. That makes the numbers a check that the documented behavior holds, with
intervals so a small sample is not mistaken for certainty. A reviewed sample drawn from real
sessions would need the evidence-lineage and review controls of the
[diagnostic corpus](diagnostic-corpus.md) and stays future work.
