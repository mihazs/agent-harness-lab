# Author objective graders for capability areas

Use this guide to plan a task bank that measures a declared area such as coding or UX/UI.
Start with [declaring and tagging areas](declare-capability-areas.md), then use
[the score guide](score-by-capability-area.md) to inspect a recorded experiment.

## Reproduce a synthetic two-area report

From a repository checkout with AHL installed, create a temporary synthetic project and
recorded experiment using the repository's test fixture. The fixture marks synthetic
tasks as reviewed to demonstrate the reporting path; it does not establish real-world
grader quality or independent roots.

```sh
export AHL_EXAMPLE_DIR="$(mktemp -d)"
python - <<'PY'
import os
from tests.support import score_fixture

project = score_fixture.project(os.environ['AHL_EXAMPLE_DIR'])
spec = score_fixture.experiment(project)
score_fixture.write(project, spec, score_fixture.records(spec))
PY
ahl score "$AHL_EXAMPLE_DIR" --spec "$AHL_EXAMPLE_DIR/experiment.json" --records "$AHL_EXAMPLE_DIR/trials.json" --format md
ahl score "$AHL_EXAMPLE_DIR" --spec "$AHL_EXAMPLE_DIR/experiment.json" --records "$AHL_EXAMPLE_DIR/trials.json" --area ux-ui
```

The records are fabricated for the example: the baseline fails and the candidate passes
each graded task. They are not results from running either harness. The starter tasks are
tagged as `coding`, `ux-ui`, both, or neither; the UX/UI label in this example does not
claim a browser or screenshot test. Remove the temporary directory when finished.

## Choose observable checks

Write a task with a concrete artifact and a verifiable condition. For coding, a grader
can run a fixed test against the submitted artifact. For UI work, choose checks tied to
the task's requirements: an accessibility check for a named control, a DOM assertion for
its label or state, or a screenshot comparison against a reviewed golden image at a
fixed viewport. Pin the browser, fonts, viewport, test data and comparison tolerance;
otherwise environmental differences can look like regressions. A screenshot difference
is a pixel-level signal, not a judgment of usability or aesthetic quality.

Keep the grader and its expected values outside the agent-visible fixture. Give the
grader fixed inputs, a stable identifier and version, and controls for broken, partial and
complete artifacts. An unreadable oracle or broken grader must produce an invalid or
unknown result rather than a task failure. The
[evaluation manifest contract](evaluation-manifest-contract.md) describes grader
bundles, controls and frozen inputs; the bundled starter bank contains executable grader
examples. Review permissions, task roots, graders and partition independence before
marking a task approved and independent. Tags alone cannot establish independence.

Do not use a subjective model rating as the objective grader. A few independent roots
give wide intervals, and an unreviewed or incomplete task may leave the area blocked or
provisional. Read the state and limitations in the
[score reference](score-reference.md) before interpreting a number.
