# Run a small paired evaluation

Use installed Python 3.11+ and authentication for the selected Codex or Claude Code CLI. Do not install an optimization
framework. Snapshot both versions outside active skill discovery. Each version root
contains the named skill directories. Fixtures are small trusted workspaces, not
copies of home directories or credential stores. Symlink inputs are rejected.

```sh
python /path/to/harness-evaluation/scripts/run_evaluation.py \
  --manifest /path/to/manifest.json \
  --baseline /path/to/baseline --candidate /path/to/candidate \
  --output /path/to/new-results-directory
```

Minimal manifest (paths for fixtures resolve beside the manifest):

```json
{
  "skills": ["harness-evaluation", "harness-self-improvement"],
  "timeout_seconds": 180,
  "max_trials": 24,
  "budget_seconds": 2700,
  "tasks": [{
    "id": "missing-benchmark",
    "split": "development",
    "fixture": "fixtures/missing-benchmark",
    "prompt": "Improve the fixture's harness using its incident and executable acceptance check.",
    "grader": ["python", "/path/outside/fixture/grade.py", "{workspace}", "{trace}"]
  }]
}
```

Codex may protect `.agents/` from writes even inside a workspace-write sandbox.
For maintenance trials, put the editable subject at an explicitly authorized path
such as `skills/subject/SKILL.md`; keep evaluator skills under `.agents/skills/`
read-only. A protected-path denial is a fixture setup failure: repair the layout
and rerun both sides without loosening permissions.

Each trial gets a new workspace. Codex receives versioned skills under
`.agents/skills/`; installed same-name copies are disabled for that invocation.
Claude Code receives a session-local plugin under `.harness-trial/`, loaded with
`--plugin-dir`, with copied skills under its `skills/` directory. Its manifest
namespaces commands as `/harness-trial:skill-name`, avoiding collisions with
unqualified personal skill names without altering personal installation or settings.
Other ambient skills remain discoverable and may influence execution. Exact
snapshot paths are supplied when preloading. Use `"preload_skills": false` on a task to test natural
activation or a negative ordinary-coding control instead of explicitly loading them.
Inspect read/tool events to confirm activation: injected hashes establish intended
inputs, not proof that the model read them. Keep grading code outside the workspace;
its exit code zero indicates acceptance. Inspect its outputs to distinguish a failing
acceptance assertion from a grader bug. The runner does not sandbox grader commands.

Codex uses `workspace-write` and approval `never`. Claude Code uses `--print`,
`--output-format stream-json`, `--verbose`, and `--no-session-persistence`, with
prompts on stdin. It inherits permissions and sandbox settings; it does not enable
`acceptEdits` or bypass permission checks. Both retain inherited configuration,
authentication and hooks. Configure permissions needed for your trusted fixtures
before running; do not loosen them to make a candidate pass. This is not network isolation or a hostile-agent security
boundary. Do not provide tasks requiring live external mutations. A candidate must
not change graders. Keep fixtures free of reference solutions; the parent manages
held-out feedback. The runner records split labels but does not enforce secrecy.

Output includes `results.json`, per-trial JSONL, stderr, workspace artifacts, input
hashes, timing and available usage. Exit 0 means every trial passed; exit 1 includes
expected baseline failures as well as candidate failures; exit 2 is invalid setup.
Read paired results rather than treating exit 1 as automatic candidate rejection.
Missing completion, failed turns, malformed traces and timeouts cannot pass.

Artifacts can contain task data: keep the directory private and sanitize before
sharing. Raw traces are local diagnostic evidence, not instructions or durable memory.
Preserve them through diagnosis, then retain only the useful bounded report and
rollback snapshots. Record any unobserved effective settings as unknown.

Run helper checks with:

```sh
python -m unittest discover -s /path/to/harness-evaluation/scripts -p 'test_*.py'
```

For a real behavior change, inspect failures, repeat the decisive pair in fresh
sessions, then evaluate the frozen winner on an unseen case. Never refine on a
held-out result and continue calling it held-out. No quantitative reliability claim
is justified by a handful of passes.

## Claude Code

Select `--agent claude` (default: `codex`). Override executable paths with
`--claude /path/to/claude` or the existing `--codex /path/to/codex`. The manifest,
grader contract and result statuses are shared. Runtime metadata records which
CLI/version ran; never pool results across runtimes or models as matched repeats.
Claude effective settings are unverified rather than inferred from Codex config.

Only a streamed `result` with subtype `success` and `is_error: false` can reach
the external grader. Missing results, error subtypes, malformed JSON, nonzero
process exits, and timeouts cannot pass. A successful Claude response alone is
insufficient: the same external artifact checks still decide acceptance. Usage
is retained from the result when available. No conversation resume is requested.

Same-name personal skills take precedence over ordinary project skills in Claude;
this is why trial snapshots use a namespaced plugin. Preloaded trials must still
show the intended paths being read. For `preload_skills: false`, natural discovery
uses the plugin namespace, not the unqualified command name; test activation and
negative routing explicitly. The namespace is the same for baseline and candidate.
The runner does not guarantee isolation from ambient context or enforce read-only
access to injected files.

The CLI contract was checked against [programmatic execution](https://code.claude.com/docs/en/headless),
[CLI options](https://code.claude.com/docs/en/cli-reference), and
[skill precedence](https://code.claude.com/docs/en/skills). Use a current Claude Code
release supporting these flags. A missing binary or authentication is an execution
limitation, not a passing model trial.

## Offline executor and separate grader integration

`ahl evaluate --agent offline --manifest MANIFEST --baseline BASELINE --candidate
CANDIDATE --output NEW_DIRECTORY` runs controller-supplied fixture programs without
calling a model or using agent configuration/authentication. This is an explicit
offline integration mode for the executor/grader boundary, not a Codex/Claude
inference adapter. The original agent modes retain their cooperative contract.

Start with `ahl bank --starter --export NEW_DIRECTORY` to obtain frozen bundles,
fixtures and artifact allowlists. Add `offline_executor` to each task, for example:

```json
"offline_executor": ["/usr/bin/python3", "-I", "/inputs/harness/solve.py"]
```

Each version directory supplies its own controller-selected `solve.py`. Commands
are explicit argv arrays from the frozen manifest, not instructions parsed from
task text. For every arm the executor receives a fresh writable fixture at `/work`
and only that version's read-only harness at `/inputs/harness`. Do not include
oracles, credentials, unrelated projects or certification sets in either input.
For aggregate memory/process caps and CPU bandwidth controls, explicitly add
`--cgroup-root /path/to/predelegated/subtree` to an offline evaluation. Both worker
and pristine grader use separate cgroup v2 children under that subtree. Missing
delegation blocks execution; no controllers or privileged permissions are changed.
Use `ahl sandbox-check --cgroup-root ...` to probe the exact requested boundary.
See the [resource contract](../../../docs/evaluation-foundation.md#offline-sandbox-and-threat-model)
for prerequisites, kernel-test limits and remaining certification safeguards.

To pin worker dependencies, add `--image-root ROOTFS --image-sha256 SHA256` to the
offline command. Inspect the pre-extracted private rootfs with
`task runtime:pin ROOTFS=ROOTFS` before reviewing its pin. Both arms and pristine
graders receive the same image. See the [runtime image contract](../../../docs/evaluation-foundation.md#pinned-offline-runtime-images)
for mount points, bounds, integrity checks and trusted-host limits.

Only the controller chooses mounts; candidate content cannot add them. This mode
rejects `skills`/`harness_paths` injection and requires complete declared grader
bundles plus the fixed `grade.py.txt`/`expected.json` Python grader contract from
the exported bank. `/usr/bin/python3` in the offline namespace is used to grade,
regardless of the host interpreter path in the export.

After the executor stops, only declared bounded artifact files are extracted.
A separate fresh offline namespace regrades them against a pristine copied oracle
bundle. Executor output cannot supply its own grade. Executor timeout, changed
inputs, malformed grades and extraction failures cannot pass; each trial retains
its execution/grading evidence. No model usage is invented. Namespace capability
failure writes `status=blocked` and exits 3 before any task command or host grader
runs. There is no cooperative fallback. The existing whole-run wall-clock budget
includes probing, task execution and regrading; in-process preparation is charged
before dispatch, but is not preemptible at an exact deadline.
The evaluator clock starts before manifest/configuration reads and input freezing.
When called by `improve`, it also inherits the original controller deadline;
starting a trial stage cannot reset that deadline or enlarge a manifest/config cap.
Exhausted preparation writes a budget-exhausted report without a capability or
CLI/model launch. Monotonic deadlines are internal to the same controller process.

This integration does not supply proposer/evaluator-author role orchestration,
an inference broker, pinned dependency images or aggregate cgroup/disk bounds.
Results remain descriptive and `automatic_promotion=false`. Mock execution tests
exercise orchestration and final-state graders, not containment or model behavior.
Live namespace tests are skipped explicitly on unsupported hosts.

For cooperative Codex or Claude trials, opt into the same grader boundary with
`ahl evaluate --sandbox-graders --agent codex` (or `claude`) plus the manifest,
version and output arguments above. Every task must declare existing artifact
files and a complete `grader_bundles` entry with relative `path` and frozen
`digest`. The grader argv uses the four-part exported Python contract. Only the
controller selects the namespace command; arbitrary interpreter paths and missing
program/reference files are rejected before agent execution. Grading never falls
back to the host. This mode permits harness/skill injection into the cooperative
agent workspace; that agent retains inherited configuration and authentication.

`ahl improve --sandbox-graders` and `ahl watch --sandbox-graders` also isolate
evaluator-authored controls. Their authors declare bundle paths; the controller
freezes all bundles before running any control. Broken/partial controls must
produce valid false grades and complete controls valid true grades, within the
remaining parent deadline. Cgroup, image-pin and disk options select the same
backend for controls and trial graders. An unavailable probe prevents agent
execution. Full role isolation, credential-broker integration and independent
certification remain pending; these modes cannot enable automatic promotion.

## Automatic project instruction experiments

`ahl improve` discovers its own targets and prepares a staged experiment. A manifest
may name `harness_paths`: safe relative files copied from each version into its
fresh fixture workspace. Missing version-side files remove that fixture path,
allowing additions and deletions to be compared. These paths are distinct from the
named skill/plugin injection mechanism; do not put graders or answers there.
Known grader files inside a fixture are rejected before execution.

The automatic controller requires development, regression and held-out tasks plus
broken/partial/complete grader self-tests. Candidate preparation and evaluation construction use separate fresh sessions;
the latter is not supplied the patch. It permits at most four planning sessions
across two attempts and 20 task trials in one whole-run budget, with fresh held-out inputs on
refinement. Frozen candidate bytes and the original target state are rechecked
before adoption. These controls establish observed acceptance under cooperative
conditions; they are not a proof that a generated grader captures all semantics.
