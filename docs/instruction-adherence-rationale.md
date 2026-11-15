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
before the rule arrived is not an ignored rule. A native capture cannot say what was in context
before its first attachment, so there the earlier calls stay unknown rather than `not_loaded`, and
a path-only or different-text attachment is not evidence that the judged file was loaded.

## Unknown stays unknown

A prohibited call is observed evidence even in a partial capture. The absence of a call is not:
the missing part may hold it, and a skipped required step needs the whole session. Even then it
is judged only after a state-changing call: a session that only answered or read may owe an
"always run X" rule nothing, so whether the rule applied is unknown. A session
whose capture state is unknown decides nothing, and a session with no declared lineage is
never counted as an independent root. Collapsing an unknown into a pass would make a harness
look better than the evidence allows, and collapsing it into a failure would invent work.

## A user's reaction is weak evidence

A user may repeat an instruction because the agent ignored it, or because they changed their
mind, answered a question or pasted the same text twice. The same holds for the other reactions
that point at an ignored instruction: a request to revert a file, a message that the task is not
done, a message sent while the agent was still acting. Each can follow an ignored instruction and
each can follow something else: the user's own edit, a new requirement, an unrelated slip.

The detector therefore reads a reaction only when it names an operand the instruction constrained,
excludes the cases it can recognize (a supersession cue, a reply straight after the agent's question)
and never lets a reaction override checkable evidence. Beside a violation the capture shows, a reaction
that came after the violating call is corroboration. Where the capture cannot decide the rule, the
reactions make one `possible_violation`, named by the strongest of them. On the shipped sample its precision is far
below the threshold, so it is reported as an abstention rather than a finding. The abstention is the
honest outcome, not a defect to tune away. A steering message is read from structure (the turn before
it is a tool call) because a captured session does not record that the user typed while the agent was
working; a runtime that records interruptions could say so directly.

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

## Why a repair plan is a recommendation

A finding says an instruction was not honored; it does not say why. The same finding can come from a
rule that never reached the agent, one buried in a long file, a hedged sentence, a prose rule that a hook
would have enforced, a constraint lost over a long session or plain agent variance. The repair plan
therefore lists every mechanism the structural facts leave open, each beside the others as a competing
explanation and always beside "the agent erred for a reason unrelated to the harness". A mechanism the
evidence cannot show, such as whether a recorded compaction removed the instruction, or a compaction nobody recorded, is `not_excluded`, never asserted. Skill
activation is listed as not assessed because the report holds no such evidence.

Only a controlled comparison can show that a repair works, and a comparison that grades itself is not
one. The screen is deliberately weaker: it applies the same detector to recorded arms, credits a rule only
when it was loaded and only for the turns after it was loaded, and counts unknown roots against the repair.
A control or preserved root where the candidate may have lost a rule blocks a supported verdict: the rule held
before the repair and cannot be decided after it (a partial capture, or a moved rule not recorded as loaded), or the
baseline could not decide it and the candidate breaks it or cannot decide it either. A root missing from the
candidate arm blocks it too, so an undecided arm never reads as a pass. It rejects a
repair that deletes a checkable rule or loses a preserved task, so a fix that makes the violation vanish by
removing the rule cannot pass.
Its best verdict says the recorded pair separates the arms and names the next step, the paired experiment.
A finding that its reproduction cannot recreate gets a recommendation and no change, and a prompt
instruction is repaired in the harness, by an echo or a re-grounding step, never by editing the prompt.
