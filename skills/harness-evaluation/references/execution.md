# Run a small paired evaluation

Use installed Python 3.11+ and Codex authentication. Do not install an optimization
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

Each trial gets a new workspace with versioned skills under `.agents/skills/`.
Installed copies of these skills are disabled for the child invocation; exact
version paths are supplied. Use `"preload_skills": false` on a task to test natural
activation or a negative ordinary-coding control instead of explicitly loading them.
Inspect read/tool events to confirm activation: injected hashes establish intended
inputs, not proof that the model read them. Keep grading code outside the workspace;
its exit code zero indicates acceptance. Inspect its outputs to distinguish a failing
acceptance assertion from a grader bug. The runner does not sandbox grader commands.

The child uses `workspace-write` and approval `never`, retaining inherited config,
authentication and hooks. This is not network isolation or a hostile-agent security
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
