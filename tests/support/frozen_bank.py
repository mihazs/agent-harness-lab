"""Synthetic frozen banks, a stub agent runtime and scripted ports for the loop adapter tests (#813). Nothing here is real data.

write_bank builds a bank folder: scoring tasks (development and regression), each its own root with a numbered fixture, and
held-out tasks whose fixtures and graders do not exist, so anything that opens one fails. A snapshot is a folder whose
AGENTS.md names the task numbers its harness solves; the stub agent writes the artifact for those only, so a trial outcome
is a function of the snapshot and the task. ScriptedRunner answers the same calls from a table, with no process at all.
"""
import hashlib
import json
from pathlib import Path
import stat
import sys

from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.loop import controller
from agent_harness_lab.loop.adapters import ExecutorRunner, FrozenBankEvaluator
from agent_harness_lab.loop.ledger import LoopLedger
from agent_harness_lab.loop.spec import resolve

RUNTIME = 'a'*64
GRADER = "import pathlib, sys\nsys.exit(0 if (pathlib.Path(sys.argv[1]) / 'artifact').read_text() == 'done' else 1)\n"
AGENT = '''#!{python}
import json, pathlib, sys
if '--version' in sys.argv: print('stub-agent 1.0'); sys.exit(0)
sys.stdin.read()
harness = pathlib.Path('AGENTS.md').read_text().strip()
if harness == 'crash': sys.exit(3)
solved = set(harness.split('=')[1].split(',')) if harness != 'solves=' else set()
pathlib.Path('artifact').write_text('done' if pathlib.Path('seed').read_text().strip() in solved else 'wrong')
print(json.dumps({{'type': 'result', 'subtype': 'success', 'is_error': False, 'usage': {{'output_tokens': 1}}}}))
'''


def write_bank(base, scoring=8, heldout=2):
    """A bank folder with `scoring` development and regression tasks t0.., one root each, and `heldout` tasks h0.. that are absent on disk."""
    folder = Path(base)/'bank'
    (folder/'grade.py').parent.mkdir(parents=True, exist_ok=True)
    (folder/'grade.py').write_text(GRADER)
    tasks = []
    for number in range(scoring):
        fixture = folder/'fixtures'/f't{number}'
        fixture.mkdir(parents=True)
        (fixture/'seed').write_text(str(number))
        tasks.append(dict(id=f't{number}', root=f'r{number}', split='development' if number % 2 == 0 else 'regression', fixture=f'fixtures/t{number}',
                          prompt='Produce the synthetic artifact', artifacts=['artifact'], grader=[sys.executable, str(folder/'grade.py'), '{workspace}']))
    for number in range(heldout):
        tasks.append(dict(id=f'h{number}', root=f'hr{number}', split='heldout', fixture=f'fixtures/h{number}', prompt='Never read',
                          artifacts=['artifact'], grader=[sys.executable, str(folder/f'missing-{number}.py'), '{workspace}']))
    manifest = folder/'manifest.json'
    manifest.write_text(json.dumps(dict(harness_paths=['AGENTS.md'], skills=[], tasks=tasks, timeout_seconds=20, budget_seconds=600, max_trials=200)))
    return manifest


def sha(label):
    """A stable digest for a scripted snapshot, which the controller requires to be a SHA-256."""
    return hashlib.sha256(label.encode()).hexdigest()


def snapshot(base, name, solves=(), text=None):
    """A harness snapshot folder and its digest; solves are the task numbers its harness gets right."""
    folder = Path(base)/'snapshots'/name
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'AGENTS.md').write_text(text if text is not None else 'solves=' + ','.join(map(str, solves)) + '\n')
    return digest(folder)


def stub_agent(base, name='claude'):
    path = Path(base)/'bin'/name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(AGENT.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def declare(trials=400, **declaration):
    """A resolved loop spec; the high alpha lets a bank of eight roots support an improvement, which a real bank would need far more for."""
    return resolve(limits=dict(seconds=600, trials=trials), project={**dict(max_rounds=2, alpha=0.9), **declaration})


class Snapshots:
    """digest -> folder; the evaluator's snapshots port."""
    def __init__(self, base): self.base = Path(base)

    def __call__(self, name):
        for folder in (self.base/'snapshots').iterdir():
            if digest(folder) == name: return folder
        raise LookupError('no such snapshot')


class Proposer:
    """Stages the given candidate digests in order and tells the evaluator which best snapshot each came from; calls records every ask."""
    def __init__(self, baseline, candidates, evaluator=None, mechanisms=('m1', 'm2', 'm3'), order=None, on_propose=None):
        self.baseline, self.mechanisms, self.left, self.evaluator, self.calls, self.order = baseline, mechanisms, list(candidates), evaluator, [], order
        self.on_propose = on_propose  # runs between the evaluation before and the evaluation after, as a change made in between would

    def propose(self, best_snapshot, mechanism):
        self.calls.append((best_snapshot, mechanism))
        if self.order is not None: self.order.append('propose')
        if self.on_propose is not None: self.on_propose()
        if not self.left: return None
        staged = self.left.pop(0)
        if self.evaluator is not None: self.evaluator.bases[staged] = best_snapshot
        return controller.Candidate(staged, mechanism)


class Recording:
    """Wraps an evaluator and notes every evaluation in the order the loop made it."""
    def __init__(self, evaluator, order):
        self.inner, self.order = evaluator, order
        self.frozen_inputs = evaluator.frozen_inputs

    def evaluate(self, snapshot, frozen_inputs):
        self.order.append('evaluate')
        return self.inner.evaluate(snapshot, frozen_inputs)

    def investigate(self, best, candidate):
        self.order.append('investigate')
        return self.inner.investigate(best, candidate)


class ScriptedRunner:
    """The runner port answered from a table: outcomes[snapshot][task] is passed, failed, any other status, or absent for no row.

    A snapshot named in ungraded has rows with no recorded grader execution."""
    def __init__(self, outcomes=None, runtime=RUNTIME, inputs_unchanged=True, ungraded=()):
        self.outcomes, self.runtime, self.inputs_unchanged, self.ungraded, self.calls = outcomes or {}, runtime, inputs_unchanged, set(ungraded), []

    def runtime_sha256(self):
        return self.runtime

    def run(self, arms, snapshots, manifest, out):
        self.calls.append(tuple(arms))
        splits = {task['id']: task['split'] for task in json.loads(Path(manifest).read_text())['tasks']}
        rows = []
        for arm in arms:
            for task, status in self.outcomes.get(snapshots[arm], {}).items():
                row = dict(task=task, version=arm, split=splits.get(task), status=status)
                if snapshots[arm] not in self.ungraded and status in ('passed', 'failed'): row['grader'] = dict(status='graded')
                rows.append(row)
        return dict(trials=rows, inputs_unchanged=self.inputs_unchanged, elapsed_seconds=1.0)


def scripted(base, outcomes, *, scoring=8, heldout=0, ungraded=(), min_roots=2, **declaration):
    """(evaluator, runner) over a fresh synthetic bank and a ScriptedRunner; a snapshot is its own name."""
    runner = ScriptedRunner(outcomes, ungraded=ungraded)
    evaluator = FrozenBankEvaluator(write_bank(base, scoring, heldout), runner, lambda name: name, Path(base)/'work', spec=declare(**declaration),
                                    min_roots=min_roots)
    return evaluator, runner


def drive(base, evaluator, proposer, spec):
    """Run the real controller over the ports into a ledger outside the project; returns (result, ledger)."""
    (Path(base)/'project').mkdir(exist_ok=True)
    ledger = LoopLedger(Path(base)/'store', Path(base)/'project')
    return controller.run(spec, evaluator, proposer, ledger, lambda: 0.0), ledger


def outcome_table(passing, scoring=8):
    """outcomes for one scripted snapshot: the tasks numbered in `passing` pass, the rest fail."""
    return {f't{number}': 'passed' if number in passing else 'failed' for number in range(scoring)}


class StubRuntime:
    """A frozen bank, snapshots and the stub agent under tmp_path, wired to the real trial runner."""
    def __init__(self, base, scoring=8, heldout=2, agent='claude'):
        from agent_harness_lab.cli import evaluator as load
        self.base = Path(base)
        self.manifest = write_bank(base, scoring, heldout)
        self.binary = stub_agent(base, agent)
        self.runner = ExecutorRunner(load().evaluate, agent, str(self.binary))
        self.snapshots = Snapshots(base)

    def snapshot(self, name, solves=(), text=None):
        return snapshot(self.base, name, solves, text)

    def evaluator(self, spec, **options):
        return FrozenBankEvaluator(self.manifest, self.runner, self.snapshots, self.base/'work', spec=spec, **options)
