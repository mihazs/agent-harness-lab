"""A synthetic project, bank and recorded paired experiment for `ahl score`; no model, network or private data.

The project is the two-area synthetic bank of `area_bank` with its tasks marked reviewed. The experiment plans every bank
task once; in the records the baseline fails and the candidate passes every graded task.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from agent_harness_lab import config
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from agent_harness_lab.evaluation.statistics import TrialRecord
from tests.support.area_bank import REGISTRY, TAGS, two_area_bank

HASHES = dict(baseline_hash='b' * 64, candidate_hash='c' * 64, grader_hash='g' * 64, runtime_hash='r' * 64)
ALL = 'all'


def project(root, *, registry=REGISTRY, tags=None, reviewed=ALL):
    """Write root/ahl.json and the tagged bank under root/bank; `reviewed` lists the task ids whose independence is declared."""
    root = Path(root)
    path = two_area_bank(root, tags)
    declared = json.loads(path.read_text())
    for task in declared['tasks']:
        if task['task_id'] == 'regression': task['family'] = 'localization'  # a root cannot straddle weighted families
        if reviewed == ALL or task['task_id'] in reviewed:
            task['independence'] = 'reviewed'
    path.write_text(json.dumps(declared))
    bank = dict(path='bank/bank.json', sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    (root / 'ahl.json').write_text(json.dumps({**config.defaults(), 'bank': bank, 'areas': registry}))
    return root


def bank_tasks(root):
    return json.loads((Path(root) / 'bank/bank.json').read_text())['tasks']


def experiment(root, *, only=None, min_roots=3, repeats=1, alpha=0.05, **fields):
    """The frozen plan over the bank's tasks (or the `only` task ids), one family per root with equal weights."""
    tasks = tuple((t['task_id'], t['root_id'], t['family']) for t in bank_tasks(root) if only is None or t['task_id'] in only)
    families = sorted({family for _, _, family in tasks})
    return ExperimentSpec('score-fixture', tasks, repeats, 7, 2 * len(tasks) * repeats, min_roots, 0.1, 0.05, alpha,
                          tuple((family, 1 / len(families)) for family in families), **{**HASHES, **fields})


def records(spec, *, ungraded=(), critical=(), arms=('baseline', 'candidate'), **hashes):
    """One record per scheduled trial of the chosen arms; an `ungraded` task has no outcome, a `critical` one regressed."""
    rows = []
    for trial in schedule(spec):
        if trial['version'] not in arms: continue
        gone = trial['task'] in ungraded
        rows.append(TrialRecord(trial['trial_id'], None if gone else trial['version'] == 'candidate', 'ungraded' if gone else 'graded',
                                'executable_grader', **{**HASHES, **hashes},
                                critical_regression=trial['version'] == 'candidate' and trial['task'] in critical))
    return rows


def write(root, spec, rows):
    """Write the spec and records beside the project; returns their paths."""
    root = Path(root)
    (root / 'experiment.json').write_text(json.dumps(spec.to_dict()))
    (root / 'trials.json').write_text(json.dumps([asdict(row) for row in rows]))
    return root / 'experiment.json', root / 'trials.json'


DOCS = dict(id='docs', label='Docs', description='Write and correct documentation.')
REVIEW = dict(id='review', label='Review', description='Find defects in a change.')


def four_states(root):
    """The synthetic project that shows every evidence state at once; returns (root, spec path, records path).

    coding is scored, ux-ui has one ungraded task (provisional), docs has no task (no_evidence) and the one review task is
    not reviewed as independent (blocked)."""
    project(root, registry=[*REGISTRY, DOCS, REVIEW], tags={**TAGS, 'resumption': ['review']}, reviewed=list(TAGS))
    spec = experiment(root)
    return (root, *write(root, spec, records(spec, ungraded=('skill-routing',))))
