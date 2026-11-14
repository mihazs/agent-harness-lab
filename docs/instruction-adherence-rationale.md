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
the missing part may hold it, and a skipped required step needs the whole session. A session
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
