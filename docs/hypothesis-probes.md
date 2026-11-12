# Why hypothesis probes are separate from analysis

This page explains a design that is specified here but not built. It gives the reasoning so a
later implementation can be reviewed against it; it is not a procedure to follow.

## The gap

Analysis can show a recurring pattern, such as the same built-in tool failing across many
reported roots and a user stepping in afterwards, without any outcome evidence. A pattern
without outcomes cannot be related to success or failure, so `no_change_evidence_insufficient`
is the honest result. Asking the maintainer to collect outcomes is a valid next step, but
slow. A probe offers a faster, narrower step: a small synthetic task that reproduces the
pattern on purpose, so a paired evaluation can support or refute one candidate repair.

## What a probe would be

A probe is a controller-built task whose fixture contains only the structural conditions of
the observed pattern (the tool class, the error category and the follow-up shape), with a
grader that checks observable state. It is run as baseline and candidate on the same
fixture, using the existing paired evaluation, and nothing else changes.

It is a hypothesis test, not a replay. The fixture is synthetic and built from the
content-free brief, never from captured prompts, paths or error bodies, so a probe cannot
leak a private history. The result answers one question: under these controlled conditions,
does this repair change the outcome of this pattern?

## Why it stays separate

A probe outcome is evidence about a constructed task. It does not show that the real
pattern caused harm, that the real sessions would have gone differently, or that
independent roots agree. Keeping it a distinct, labeled record preserves the existing
distinction between observed patterns, supported diagnoses and validated improvements.
A passing probe is at most a supported hypothesis, and promotion still requires the
normal evaluation and adoption path.

## What is deliberately left out

- No automatic probe generation inside analysis: analysis stays free of model calls.
- No probe-derived success label for the original sessions; unknown stays unknown.
- No reuse of a probe as a held-out task for the repair it was built to test.

## Status

Not implemented. The follow-up work is tracked in [#708](https://github.com/mihazs/agent-harness-lab/issues/708). The brief already lists
a `targeted_probe` entry under missing evidence whose command runs a focused `ahl improve`;
that is a focus hint, not a probe.
