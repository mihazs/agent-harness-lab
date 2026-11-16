# Write an objective grader for a capability area

Use this when a task of a capability area, such as UX/UI, needs a grader that
[`ahl score`](score-by-capability-area.md) can count. An area is only as measurable as its
graders: a task without an executable grader that returns yes or no leaves its area
`no_evidence` or `provisional`, and a task nobody has reviewed leaves it `blocked`.

An objective grader looks at what the agent left behind and answers from the observation:
a DOM assertion, an accessibility rule or a pixel comparison against a golden image. It
never asks a model whether the result looks good, and it never reads the agent's own claim
that the work is done. The [limits](#what-these-graders-cannot-judge) say what that leaves out.

## Before you start

- The area is [declared and the task is tagged with it](declare-capability-areas.md).
- The task's fixture and prompt exist. This page covers only the grader bundle.
- You have Python 3.11 or newer. The example grader uses the standard library only.

## 1. Follow the grader contract

A grader is a program, kept in the task's bundle as `grade.py.txt` beside its
`expected.json`. It is run as:

```text
python3 grade.py.txt WORKSPACE expected.json
```

`WORKSPACE` is the directory of the agent's declared artifacts. The grader prints one JSON
line and exits with a matching code:

| Result | `valid` | `outcome` | Exit |
| --- | --- | --- | --- |
| The checks hold | `true` | `true` | 0 |
| A check fails, or the agent delivered nothing usable | `true` | `false` | 1 |
| The grader cannot decide (unreadable reference, unknown rule, unusable golden image) | `false` | `null` | 2 |

The line also carries `schema_version: 1`, a `grader_id` and a `grader_version`. The third
row matters most for scoring. An unknown outcome is never read as a pass, a fail or a proof of
independence: a grader that cannot decide must say so with exit 2 instead of guessing
`false`, and a missing file of the agent is the agent's failure, so it is `false`.

## 2. Choose checks that observe state

Start from the [example grader](../examples/area-grader/grade.py.txt) and its
[expected.json](../examples/area-grader/expected.json). It reads an HTML page and a
screenshot from the workspace and applies three kinds of check, each declared in
`expected.json`:

| Check | What it observes | Declared as | Watch for |
| --- | --- | --- | --- |
| DOM assertion | An element with a tag, attributes and exact text exists in the page. | `dom`: a list of `{tag, attrs, text}`. | Assert the element a user needs, not the markup around it. `attrs` and `text` are optional. |
| Accessibility rule | A named rule holds for the whole page: `html_lang`, `img_alt` (an image has alt text, or `role="presentation"`) or `control_label` (every form control has an `aria-label` or a `label for`). | `accessibility`: a list of rule names. | A rule says the page is not inaccessible in one way. It does not say it is accessible. |
| Screenshot diff | The rendered pixels equal a golden image within a per-channel tolerance and a share of changed pixels. | `screenshot`: `actual`, `golden`, `channel_tolerance`, `max_changed_fraction`. | See below: who renders the image decides whether the check means anything. |

All declared checks must hold, and a reference that declares none is invalid (exit 2), so a
grader can never pass because it checked nothing.

Three rules keep the checks objective:

1. **Render in the grader.** The example reads `shot.png` from the workspace so that it
   needs no browser. In a real task, call your pinned renderer on the agent's final
   artifact from inside the grader. An image the agent submits can be a copy of the golden
   image, and the check would pass without the page ever being rendered.
2. **Keep the golden image in the bundle.** It sits beside `expected.json`, so its bytes are
   part of the bundle's digest and the agent cannot reach it. Change it only by creating a new
   grader version, never to make a candidate pass.
3. **Pin what the pixels depend on.** The same page renders differently with another browser,
   font or scale. Pin the renderer and its fonts, and use the tolerance for anti-aliasing,
   not to hide real differences.

Run the example against its three controls:

```sh
python3 examples/area-grader/grade.py.txt examples/area-grader/controls/complete examples/area-grader/expected.json
python3 examples/area-grader/grade.py.txt examples/area-grader/controls/partial examples/area-grader/expected.json
python3 examples/area-grader/grade.py.txt examples/area-grader/controls/broken examples/area-grader/expected.json
```

They print `"outcome": true` (exit 0), then `"outcome": false` for a page whose image has no
alt text (exit 1) and `"outcome": false` for a page that is not the interface at all
(exit 1).

## 3. Add controls before you trust the grader

Each task needs controls: directories of synthetic artifacts that the grader must sort
correctly before any agent runs. `broken` and `partial` must give `false`, `complete` and
`alternative_valid` (a different correct solution) must give `true`, and `superficial` (a
change that looks right but misses the point) must give `false`. The example has all five.
Its `partial` control has a perfect screenshot, and only the missing alt text fails it. Its
`superficial` control has exactly the right markup and renders a blank page, so only the
pixels notice.

List the controls in `controls.json` and audit them. The audit executes your grader code on
your machine, so use it only for graders you wrote or reviewed:

```sh
ahl bank --manifest bank/bank.json --audit --cooperative-controls
```

A grader that passes the untouched fixture is rejected as `vacuous_success`: a check the
starting point already satisfies measures nothing. The bank rules are in
[bank declarations and qualification](bank-qualification.md).

## 4. Mark the review honestly

Set `independence: "reviewed"`, `review_status: "accepted"` and `permission: "approved"` on
the task only after a person has reviewed it. `ahl score` does not verify these
declarations, but without them the area is `blocked`, and with a false declaration it
reports a `scored` area that nobody has shown to be independent.

## What these graders cannot judge

- **Aesthetics.** Whether a layout is balanced, a palette fits the brand or a flow feels
  right is not observable by these checks, and a model that rates it would be a subjective
  grader, which `ahl score` does not accept. UX/UI here means observable behavior and
  structure.
- **Quality from sameness.** A golden diff says the page equals one rendering. A better design
  fails it, and a different valid design needs its own `alternative_valid` control.
- **Accessibility as a whole.** Three rules are three rules. Add the rules your project
  needs, and keep claiming only what a rule observes.
- **Enough evidence from a few tasks.** A task adds one independent root, and an interval
  half-width of 1.57 with three roots covers the whole range. See
  [what a per-area score cannot tell you](score-by-capability-area.md#what-a-per-area-score-cannot-tell-you)
  for how the interval narrows.

Next, [score the area](score-by-capability-area.md) from a recorded paired experiment.
