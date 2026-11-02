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

The offline command mode does not perform model inference or role construction.
Dependency images and aggregate cgroup/disk bounds use the explicit profiles above.
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

## Controller-owned Responses executor

`ahl evaluate --agent responses` adds an explicit controller-owned OpenAI Responses
executor. It is separate from the cooperative Codex/Claude CLI modes. The same
profile also supports controlled proposer/evaluator construction through `improve`
and `watch`, using the role contract below. Automatic promotion remains unavailable.

Supply the manifest/version/output arguments above, plus all of:

```sh
--inference-settings /absolute/private/inference-settings.json \
--cgroup-root /absolute/delegated/subtree \
--image-root /absolute/private/rootfs --image-sha256 REVIEWED_SHA256 \
--disk-bytes 67108864
```

The settings file has an exact versioned schema; no environment/default account
or model discovery occurs. An example with explicit operator-selected caps is:

```json
{
  "schema_version": 1,
  "model": "operator-selected-model",
  "credential_file": "/absolute/private/key.txt",
  "max_requests": 40,
  "max_tool_calls": 32,
  "max_output_tokens": 512,
  "max_request_bytes": 1048576,
  "max_response_bytes": 1048576
}
```

The key file must be an owner-only, single-link regular file outside worker views,
runtime images and output directories. Keep settings/keys outside version control.
The controller probes the selected boundary and admits all initial fixtures before
reading the key. Missing/unsupported boundaries block; no native CLI or cooperative
fallback is launched. Tasks require frozen grader bundles and explicit artifacts.
Use version directories containing only the intended harness. Skill/harness-path
injection and cooperative `--config` profiles are not supported in this mode.

Inference goes only to `https://api.openai.com/v1/responses`. A trusted isolated
Python controller child receives credentials through a private stdin pipe, with a
cleared environment and no site startup hooks. It follows no redirects or proxy
settings and exits before any worker command runs. The parent terminates stalled
transport at its remaining deadline and never records provider error bodies or
credentials. TLS/live API compatibility has not been exercised by synthetic tests.

The model gets one strict `run(argv)` tool. All complete output items and arguments
are checked before dispatch; hosted tools, unknown functions, duplicate IDs and
malformed/incomplete replies abort. Each command runs offline with only its task
state and selected readonly harness. Worker processes receive no broker token,
credential file, provider socket or controller environment. Replies are replayed
with encrypted reasoning and `store=false`, following the [Responses migration
contract](https://developers.openai.com/api/docs/guides/migrate-to-responses).
This setting alone is not a Zero Data Retention guarantee.

Requests and tool reservations are capped across the entire paired run.
`max_output_tokens` is per request; the whole-run reservation ceiling is
`max_requests * max_output_tokens`. Byte limits bound each request/context and
response frame. These are explicit execution caps, not a monetary limit or exact
input-token plan. Reports separate reserved output from provider-reported usage.
Ambiguous costed failures or containment failures stop further inference; no retry
or cheaper-model fallback occurs. Preparation, transport, commands, snapshots and
grading share the original wall-clock budget.

Between commands, complete working state stays in controller memory: at most 100
regular single-link files, 1 MiB contents, 100 directories, depth 64, 4096-byte paths
and 64 KiB metadata. Capture occurs after worker cleanup through no-follow directory
descriptors. Preserve ordinary modes, new files, empty directories and deletions;
reject links, special files/mode bits and incomplete/oversized reads. Nonzero test
commands with exit codes 1–127 retain valid edits for inspection. Higher codes are
ambiguous with wrapped signals and abort. Timeout/resource/integrity failures
and signal-killed commands never adopt partial state or continue inference.
Only terminal declared artifacts are staged and
published together for fresh offline grading; a final model claim cannot pass a
task. Synthetic transports establish orchestration; separately selected kernel
canaries establish only their tested process/filesystem boundaries.

### Controlled improvement roles

Select `ahl improve PROJECT --agent responses` (or `watch`) with the same explicit
inference settings, cgroup, pinned-image and disk arguments above. The profile
uses one client and original deadline across preparation, every task and one
possible refinement; no stage resets the request/tool limits or reads another key.
Native binary discovery, plugin setup and native agent/grader execution are outside
this branch. Missing settings or resource profiles fail; no mixed native fallback
occurs. Existing Codex/Claude behavior keeps its cooperative contract.

| Role | Writable state | Readonly inputs |
| --- | --- | --- |
| Proposer | Candidate subtree and proposal document | Baseline, projected evidence, owned methodology; filtered refinement feedback |
| Evaluator author | New manifest, fixtures, controls and complete bundles | Baseline, projected evidence and evaluation methodology |
| Executor | Fresh bounded task state | Selected frozen harness |
| Grader | Fresh scratch only | Final bounded artifacts and pristine oracle |

The metadata projection contains pattern kinds/explanations, activity/count/coverage
summaries and limitations. Source paths, event identity references and prior
evaluation outcomes are excluded. The original project, raw captures and run
directories are not mounted. This is a narrower inspection view than the native
workflow. Refinement gets only task/version/split/status for development/regression
rows. The author never receives that feedback, candidate/proposal files or
candidate-derived filenames. Each session has a fresh history; controller counters
remain shared. Context methods omit generated bytecode/cache files.

Complete authored trees publish atomically with their accepted ordinary modes and
deletions, after bounded capture. Candidate scope is checked before freezing.
Authors supply relative bundle paths, empty skills/harness_paths and no grader
argv/digests. The controller constructs the exact Python argv and freezes all
bundle digests before offline controls. Task execution mounts the full selected
harness instead of injecting candidate-derived file lists.

Private `inference.json` receipts persist reservations before dispatch and complete
provider usage afterward. Pending/blocked flags retain ambiguous failures without
retry, provider error bodies or credentials. Settings changes abort the current
run; watch fingerprints include settings path/content without reading a key or
probing native binaries. API/protocol failures return structured error/budget
dispositions and require intervention, rather than automatic transient retries.

Synthetic full workflows test shared limits and refinement; selected kernel tests
run all roles with a pinned image and attempt forbidden source/home/key,
candidate/proposal and prior-heldout reads. These are implementation-specific
controls, not live API compatibility, independent task lineage or effective model
verification. Qualification and automatic behavioral adoption remain blocked.

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
