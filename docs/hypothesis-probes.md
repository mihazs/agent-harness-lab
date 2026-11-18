# Why hypothesis probes are separate from analysis

This page explains the design and the limits of the current synthetic probe.

## The gap

Analysis can show a recurring pattern, such as the same built-in tool failing across many
reported roots and a user stepping in afterwards, without any outcome evidence. A pattern
without outcomes cannot be related to success or failure, so `no_change_evidence_insufficient`
is the honest result. Asking the maintainer to collect outcomes is a valid next step, but
slow. A probe offers a faster, narrower step: a small synthetic task that reproduces the
pattern on purpose, so a paired evaluation can support or refute one candidate repair.

## What a probe is

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

## Running the current probe

The current fixture supports a shell tool, a reported tool error or nonzero exit,
and an unambiguous same-call retry or changed-input follow-up. Other patterns are refused rather than
represented by a different task. Select the zero-based mechanism index in a
private analysis brief and provide two distinct harness snapshots:

```sh
ahl hypothesis-probe --brief /private/brief.json --mechanism 0 \
  --project /path/to/project --baseline /private/baseline --candidate /private/candidate \
  --output /private/new-probe --agent codex --binary codex --harness-path AGENTS.md
```

The output directory must be new and separate from the project and snapshots.
The command runs a cooperative agent twice; the agent's own permissions still apply.
The task asks for the output without prescribing a repair. The development task records a failed synthetic worker attempt and a later successful
attempt, and the grader checks their content-free call shapes and the final files. This log is written
inside the agent workspace: it is not an independent or tamper-proof tool trace, and
does not prove which tool was used. The result describes one synthetic pair only.

The generated fixture is marked as a probe. Final-bank and held-out validation reject
the marker and an unchanged generated worker even if the marker is removed. These
checks cannot recognize every edited derivative; maintainers must preserve provenance
and exclude all probe-derived tasks from the repair's held-out evaluation. They do not
establish independence or authorize promotion.

The brief's `targeted_probe` entry runs a focused `ahl improve`; it remains a focus hint,
not this probe.
