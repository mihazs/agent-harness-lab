# Guarded development search

The opt-in Python search API can execute comparisons through the actual private
controller and verify its receipts. Callback `safe`, metric or certification JSON
does not qualify evidence in this mode. The default-off legacy callback route
remains explicitly `unverified_callback`.

Start with a current controlled-bank export containing frozen development
preservation cohorts and reviewed prior qualification. The exported experiment
must match the baseline harness and pinned runtime. Guarded search preserves task,
root, family, oracle, cohort, seed, repeat, weight and margin identities. It derives
stage identities and candidate harness pins itself.

Construct `HistorySearchContext(store_path, baseline_dir, frozen_manifest,
resource_profile, allowed_files)`. The resource profile has exactly these fields:

- `cgroup_root`: existing delegated controller scope.
- `image_root` and `image_sha256`: the pinned extracted offline runtime.
- `disk_bytes`: the existing per-run private scratch limit.
- `budget_seconds`: the bounded per-stage limit.
- `work_root`: private persistent storage for prepared inputs and genuine run custody.

The work root must remain disjoint from controller history, baseline, export,
controlled-bank and image trees. The search state belongs in a separate owner-only
directory outside all those trees, including the work root. Keep both private:
state contains candidate text and private evidence references.

```python
from agent_harness_lab.optimization.budget import Budget
from agent_harness_lab.optimization.history_evidence import HistorySearchContext
from agent_harness_lab.optimization.search import CandidateRecord, search

# Supply actual paths, pinned resources and a reviewed proposal for this experiment.
context = HistorySearchContext(
    store_path, baseline_dir, frozen_manifest, resource_profile, ["solve.py"]
)
baseline = CandidateRecord(
    "baseline", (), "control", {}, ("private:comparison-plan",),
    "Retain the frozen baseline", "Any changed input invalidates this comparison"
)
result = search(
    state_path, baseline, propose, None, None,
    Budget(ceiling=5, reserve=1), ["solve.py"],
    enabled=True, variants=1, controller=context
)
```

`propose(index)` returns a bounded authorized `CandidateRecord`. The controller
copies the exact baseline, applies that patch and independently hashes the full
prepared tree. A patch-text digest is not a harness identity. Only declared
fixtures, oracle bundles, controls and cohort metadata enter prepared input views.
File, directory, byte, path and attempt limits are checked before allocation.

Charges count controller stages, not physical trials, tokens or dollars. The final
reserve funds fresh development confirmation. Individual run limits remain in
force. The overall deadline starts before live preflight and defaults to the
resource profile's budget; an explicit finite monotonic `parent_deadline` can
declare a different aggregate workflow window. Verification cannot reset or exceed
that deadline to return a favorable result.

The A/A baseline does not need positive superiority. It does need complete current
evidence, no critical veto or required unknown observation, and sufficiently precise
family/cohort noninferiority. Sparse evidence can retain the no-op without claiming
qualified feasibility. A proposed improvement needs supported primary, family and
every configured required cohort endpoint. Only verified primary effect ranks
candidates; missing measured costs do not become zero.

A favorable selection requires another fresh execution of both comparison arms
and a distinct confirmation receipt. Its status is
`development_recommended_for_review`, with automatic promotion, publication and
adoption authority all false. This confirmation uses development evidence; it is
not untouched certification. Native identity and semantic root independence remain
unverified, and statistical assumptions stay explicit.

Schema-2 state persists charges, frozen context, candidate bindings, actual run IDs
and receipt references before dispatch. Resume revalidates relevant evidence and
context without redispatching charged interrupted stages or refunding costs.
Revocation, changed inputs, incompatible state or engine changes block reuse.
Receipt regeneration and rebinding after an engine change must be explicit.

Use [private history reports](history-reports.md) to inspect comparison receipts.
Public export is refused even if search state is relabelled synthetic. Oracle
corrections use a separately reviewed
[correction-rerun request](grader-corrections.md#requalify-corrected-preservation-cohorts)
to execute fresh qualification, freeze a new cohort and rerun both comparison arms.

Run `task test:optimization:history` for native controller/search contracts.
The actual synthetic positive control explicitly predeclares an affordable test
policy; production defaults are unchanged. A separate default-policy thousand-root
control checks conditional statistics. Neither establishes real model benefit.
