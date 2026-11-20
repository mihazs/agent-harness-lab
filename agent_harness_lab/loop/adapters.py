"""The loop's evaluator port over the real evaluation: a frozen scoring bank, an absolute score with an interval, a vacuity gate.

FrozenBankEvaluator implements controller.Evaluator. It scores each snapshot on the same frozen bank: only the development
and regression tasks, so no held-out or certification input is opened. The first evaluation runs the baseline arm alone;
every later one is a fresh paired run, and old trials are never reused. It adds no statistics: the interval is
statistics.bound over per-root mean outcomes, the paired comparison is evaluation.before_after and the labels are
evaluation.vacuity. Recommendation only: nothing here applies, promotes or certifies a candidate.

Unknown never becomes a result: an outcome that is not passed or failed leaves its root unscored, an unreadable or changed
input leaves the evaluation's inputs unknown, and either keeps the evaluation from being valid.
"""
from argparse import Namespace
from contextlib import nullcontext
import hashlib
import json
import math
from pathlib import Path
import shutil

from agent_harness_lab.evaluation import area_gate, before_after, integrity, statistics, vacuity
from agent_harness_lab.evidence_label import grader_fingerprint
from agent_harness_lab.monitoring.state import atomic_json
from agent_harness_lab.sources.schema import digest

from .controller import Evaluation
from .errors import LoopError

PROFILE = 'ahl-loop-frozen-bank-v1'
SCORING = ('development', 'regression')  # the partitions that choose a candidate; any other split is never run or read
SCORING_MANIFEST = 'loop-scoring-manifest.json'
OMITTED = ('experiment', 'cohort_manifest', 'protected_access')  # a plan or certification access never reaches the loop
GRADED = {'passed': 1, 'failed': 0}
CAUSE = {'candidate_caused': 'candidate', 'pipeline_caused': 'other'}  # undetermined is unknown


def absolute_score(tasks, rows, *, alpha, min_roots):
    """The score of one arm: the mean of per-root mean outcomes in [0, 1] with its statistics.bound interval.

    tasks are (task id, root id) pairs; rows the arm's recorded trials. A task counts only with exactly one row ending passed
    or failed, and a root only when all its tasks count, so an unknown outcome is never read as either. The evidence is
    scored with at least min_roots roots and none unknown, provisional otherwise, no_evidence with no root. The radius is
    derived for differences in [-1, 1]; on [0, 1] it is valid but conservative.
    """
    found = {}
    for row in rows: found.setdefault(row.get('task'), []).append(row.get('status'))
    roots = {}
    for task, root in tasks:
        statuses = found.get(task, [])
        status = statuses[0] if len(statuses) == 1 and isinstance(statuses[0], str) else None
        roots.setdefault(root, []).append(GRADED.get(status))
    known = [sum(values)/len(values) for values in roots.values() if None not in values]
    unknown = len(roots) - len(known)
    if not known: return dict(point=0.0, lower=0.0, upper=1.0, evidence='no_evidence', roots=0, unknown_roots=unknown)
    point = sum(known)/len(known)
    interval = statistics.bound(known, [1/len(known)]*len(known), alpha)
    return dict(point=point, lower=min(point, max(0.0, interval['low'])), upper=max(point, min(1.0, interval['high'])),
                evidence='scored' if not unknown and len(known) >= min_roots else 'provisional', roots=len(known), unknown_roots=unknown)


def scoring_manifest(path):
    """Write the scoring manifest beside the bank's manifest and return (its path, how many tasks it left out).

    It holds the tasks whose split is development or regression, with their graders and controls, and nothing that declares
    a plan or certification access; the other tasks, their fixtures and graders are not touched. An unusable or certification
    bank is an adapter defect.
    """
    path = Path(path)
    try:
        manifest = json.loads(path.read_text())
        experiment = manifest.get('experiment')
        if 'protected_access' in manifest or isinstance(experiment, dict) and experiment.get('profile') == 'certification': raise ValueError
        if not isinstance(manifest['tasks'], list): raise ValueError
        tasks = [task for task in manifest['tasks'] if isinstance(task, dict) and task.get('split') in SCORING]
        ids = {task['id'] for task in tasks}
        if len(ids) != len(tasks) or not all(isinstance(task_id, str) and task_id for task_id in ids): raise ValueError
        kept = {key: value for key, value in manifest.items() if key not in OMITTED}
        kept['tasks'] = tasks
        if 'grader_self_tests' in kept: kept['grader_self_tests'] = [item for item in kept['grader_self_tests'] if isinstance(item, dict) and item.get('task') in ids]
        if 'grader_bundles' in kept: kept['grader_bundles'] = {name: item for name, item in kept['grader_bundles'].items() if name in ids}
        target, text = path.with_name(SCORING_MANIFEST), json.dumps(kept, indent=2, sort_keys=True) + '\n'
        if target.exists() and target.read_text() != text: raise ValueError  # the frozen scoring bank is never rewritten
        target.write_text(text)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise LoopError('loop_port_invalid', 'bank', 'declared') from error
    return target, len(manifest['tasks']) - len(tasks)


class ExecutorRunner:
    """The existing trial runner bound to one agent: one call runs the named arms of the scoring manifest.

    evaluate is run_evaluation.evaluate; binary the agent executable; options extra evaluate arguments such as inherit_env.
    """
    def __init__(self, evaluate, agent, binary, **options):
        self.evaluate, self.agent, self.binary, self.options = evaluate, agent, binary, options

    def runtime_sha256(self):
        """The launch identity both arms share, read now so a changed agent or executable changes the frozen inputs."""
        return before_after.launch_sha256(self.agent, shutil.which(self.binary) or self.binary, self.options.get('inherit_env', False))

    def run(self, arms, snapshots, manifest, out):
        options = dict(self.options)
        deadline = options.pop('deadline', None)  # a callable returning the monotonic time the shared budget ends, so each run is capped by it
        if deadline is not None: options['parent_deadline'] = deadline()
        self.evaluate(Namespace(manifest=str(manifest), output=str(out), agent=self.agent, codex=self.binary, claude=self.binary,
                                arms=tuple(arms), **{arm: str(snapshots[arm]) for arm in arms}, **options))
        return json.loads((Path(out)/'results.json').read_text())


def baseline_observations(tasks, trials):
    """Expose graded scoring-task descriptions and outcomes without held-out data."""
    prompts = {task['id']: task.get('prompt', '')[:4000] for task in tasks
               if task.get('split') in SCORING and isinstance(task.get('prompt'), str)}
    return [dict(task=row['task'], status=row['status'], prompt=prompts[row['task']])
            for row in trials if isinstance(row, dict) and row.get('task') in prompts
            and row.get('version') == 'baseline' and row.get('status') in ('passed', 'failed')]


class FrozenBankEvaluator:
    """controller.Evaluator over one frozen scoring bank.

    manifest is the bank's manifest, runner an ExecutorRunner or anything with runtime_sha256() and run(arms, snapshots,
    manifest, out) returning the results.json shape, snapshots maps a snapshot digest to its directory, work an owner-only
    directory outside the bank and the snapshots, spec the resolved LoopSpec: its per-look alpha, regression margin and noise
    margin bound every interval and paired comparison. min_roots is the plan minimum of independent roots. areas, when the project
    declares any, is {declared: area ids, selected: the --area ids}: each paired run is then also gated per area
    (evaluation.area_gate) and an area that lost paired outcomes beyond the regression margin is recorded as a regressed area.

    The bank, grader, seed schedule, per-arm budget share and runtime are hashed once into frozen_inputs and observed again
    around every run; an evaluation reports the digest it observed, so a difference is comparison_invalid in the controller.
    bases maps a candidate snapshot to the best snapshot it was staged from, which is the other arm of its paired run (the
    first evaluated snapshot when unrecorded). runs keeps what each evaluation saw.
    """
    def __init__(self, manifest, runner, snapshots, work, *, spec, min_roots=2, areas=None):
        self.runner, self.snapshots, self.work, self.spec, self.min_roots = runner, snapshots, Path(work), spec, min_roots
        self.areas = areas or None
        self.scoring, self.excluded = scoring_manifest(manifest)
        scoring_tasks = json.loads(self.scoring.read_text())['tasks']
        self.tasks = [(task['id'], task.get('root') if isinstance(task.get('root'), str) else task['id']) for task in scoring_tasks]
        self.scoring_tasks = scoring_tasks
        self.identity = self._observe()
        if not self._known(self.identity): raise LoopError('loop_port_invalid', 'frozen_inputs', 'declared')
        self.frozen_inputs = self.identity['identity_sha256']
        self.bases, self.runs, self.constructions, self.baseline = {}, [], 0, None
        self.baseline_observations = []
        self.blocker = None

    @classmethod
    def first_use(cls, construct, *args, stages=None, **options):
        """No configured bank: run the construction stage once, charged to its stage budget (#739), and freeze what it returns.

        construct returns the manifest path. It is called here and never again, so every later round scores on this bank."""
        with stages.charge('construction') if stages is not None else nullcontext():
            manifest = construct()
        evaluator = cls(manifest, *args, **options)
        evaluator.constructions = 1
        return evaluator

    @staticmethod
    def _known(record):
        return all(record[name] is not None for name in before_after.IDENTITY_FIELDS)

    def _observe(self):
        """The five frozen-input facts as they are on disk now; one that cannot be read is None, which is unknown."""
        try:
            manifest, folder = json.loads(self.scoring.read_text()), self.scoring.parent
            fixtures = {task['id']: integrity.digest(folder/task['fixture']) for task in manifest['tasks']}
            return before_after.frozen_inputs(
                bank_sha256=digest(dict(manifest=before_after.bank_sha256(manifest, folder), fixtures=fixtures)),
                grader_sha256=grader_fingerprint(self.scoring, portable=True),
                seed_schedule_sha256=before_after.schedule_sha256(manifest['tasks']), budget_share=dict(trials=len(manifest['tasks'])),
                runtime_sha256=self.runner.runtime_sha256())
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return before_after.frozen_inputs()

    def _run(self, arms, snapshots, index):
        """The runner's results; a bank with no scoring task runs nothing and spends nothing."""
        if not self.tasks: return dict(trials=[], inputs_unchanged=True, elapsed_seconds=0.0)
        return self.runner.run(arms, {arm: self.snapshots(snapshots[arm]) for arm in arms}, self.scoring, self.work/f'evaluation-{index}')

    def evaluate(self, snapshot, frozen_inputs):
        if frozen_inputs != self.frozen_inputs: raise LoopError('loop_port_invalid', 'frozen_inputs', 'declared')
        index = len(self.runs)
        paired = index > 0
        against = self.bases.get(snapshot, self.baseline)
        arm, arms = ('candidate', ('baseline', 'candidate')) if paired else ('baseline', ('baseline',))
        before = self._observe()
        ran = before == self.identity  # inputs that already differ would spend trials on a comparison that cannot be valid
        result = self._run(arms, {'baseline': against if paired else snapshot, 'candidate': snapshot}, index) if ran else dict(trials=[], elapsed_seconds=0.0)
        if result.get('status') in ('blocked', 'budget_exhausted') and isinstance(result.get('reason'), str):
            self.blocker = dict(status=result['status'], reason=result['reason'], stage='candidate' if paired else 'baseline')
        unchanged = not ran or self._observe() == before and result.get('inputs_unchanged') is True  # a run that moved an input leaves them unknown
        observed = before if unchanged else before_after.frozen_inputs()
        trials = result.get('trials')
        ids = dict(self.tasks)
        if not paired:
            self.baseline_observations = baseline_observations(self.scoring_tasks, trials) if isinstance(trials, list) else []
        recorded = [row for row in trials if isinstance(row, dict) and row.get('task') in ids] if isinstance(trials, list) else []
        mine = [row for row in recorded if row.get('version') == arm]
        labels = vacuity.classify([dict(id=task) for task, _ in self.tasks], mine, baseline_sha256=against if paired else None, candidate_sha256=snapshot) if ran else []
        score = absolute_score(self.tasks, mine, alpha=self.spec.per_look_alpha, min_roots=self.min_roots)
        seconds = result.get('elapsed_seconds')
        seconds = seconds if type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0 else None
        reserve = {} if paired else dict(reserve_trials=2*len(self.tasks), reserve_seconds=None if seconds is None else 2*seconds)
        pairs = before_after.run_pairs(json.loads(self.scoring.read_text()), recorded) if paired or observed != self.identity else None
        gate = None
        if self.areas and paired and ran:  # a per-area loss is a regression the pooled score can hide
            gate = area_gate.gate(pairs, self.areas['declared'], selected=self.areas.get('selected', ()), margin=self.spec.regression_margin,
                                  alpha=self.spec.per_look_alpha, min_roots=self.min_roots)
        found = Evaluation(score['point'], score['lower'], score['upper'], evidence=score['evidence'] if ran else 'blocked', vacuity=labels[0] if labels else None,
                           trials=len(trials) if isinstance(trials, list) else None, seconds=seconds,
                           inputs=observed['identity_sha256'] if self._known(observed) else None,
                           regressed_areas=tuple(gate['regressed']) if gate else (), **reserve)
        run = dict(index=index, snapshot=snapshot, arms=arms, against=against if paired else None, labels=labels, score=score,
                   identity=observed, evaluation=found, guard=None, pairs=None, investigation=None, area_gate=gate)
        if pairs is not None:
            run['pairs'] = pairs
            run['guard'] = before_after.decide(run['pairs'], baseline=self.identity, candidate=observed, margin=self.spec.regression_margin,
                                               gain_margin=self.spec.noise_margin, alpha=self.spec.per_look_alpha)
            if run['guard']['frozen_inputs']['comparison']['status'] != 'identical': self._investigate(run)  # the AHL defect gets its artifact
        self.runs.append(run)
        if self.baseline is None: self.baseline = snapshot
        return found

    def _investigate(self, run, **known):
        artifact = before_after.investigate(run['pairs'], run['guard'], **known)
        path = self.work/f'investigation-{run["index"]}.json'
        atomic_json(path, artifact)
        run['investigation'] = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), cause=artifact['cause']['class'])
        return artifact

    def investigate(self, best_evaluation, candidate_evaluation):
        """Who a regression is blamed on, from the paired run's investigation artifact: candidate, other or None for unknown.

        Asked only about an evaluation that ran on the frozen inputs, so the grader and inputs are known to be unchanged."""
        run = next((item for item in self.runs if item['evaluation'] is candidate_evaluation), None)
        if run is None or run['guard'] is None: return None
        artifact = self._investigate(run, grader_changed=False, inputs_unchanged=True)
        return CAUSE.get(artifact['cause']['class'])
