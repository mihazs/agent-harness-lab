"""Seeded synthetic TrialRecord banks for the Bayesian decision wiring; no private data.

Each bank is built from the shapes in ``bayes_scenarios``; its expected verdict follows from the true paired
effect and the number of roots chosen before any score was computed, never from adjusting numbers afterwards.
"""
import contextlib
from dataclasses import asdict, replace
import io
import json
from pathlib import Path
import random

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.plan import schedule
from agent_harness_lab.evaluation.statistics import TrialRecord
from tests.support.bayes_scenarios import SHAPES, outcome_pair, plan

REPEATS = 4  # repetitions averaged inside one root
KINDS = ('clear_win', 'clear_loss', 'tie', 'mixed_unknowns', 'critical_veto', 'starter_bank')
EXPECTED_VERDICTS = Path(__file__).resolve().parents[2] / 'examples' / 'bayes-decision' / 'expected-verdicts.json'


def expected_verdicts():
    """kind -> {verdict, reason, stage, analysis}: the committed expectations, fixed from each bank's construction."""
    return json.loads(EXPECTED_VERDICTS.read_text())['fixtures']


def trial_records(spec, pair_of, *, unknown=0.0, critical=0, rng=None):
    """One record per scheduled trial; ``pair_of(trial, rng)`` gives one (candidate, baseline) pair per task repetition."""
    rng, pairs, rows, flagged = rng or random.Random(5), {}, [], 0
    for trial in schedule(spec):
        key = trial['task'], trial['repetition']
        pair = pairs.setdefault(key, pair_of(trial, rng))
        outcome = pair[trial['version'] == 'baseline']
        ungraded = rng.random() < unknown
        flag = trial['version'] == 'candidate' and flagged < critical
        flagged += flag
        rows.append(TrialRecord(trial['trial_id'], None if ungraded else outcome, 'ungraded' if ungraded else 'graded',
                                'executable_grader', spec.baseline_hash, spec.candidate_hash, spec.grader_hash,
                                spec.runtime_hash, critical_regression=flag))
    return rows


def bank(kind, *, draws=4000, seed=11, **options):
    """Return dict(spec, records) for one of the six named fixtures."""
    if kind == 'starter_bank':  # six roots, every one a candidate win
        spec = plan(3, min_roots=2, draws=draws, repeats=REPEATS, **options)
        return dict(spec=spec, records=trial_records(spec, lambda trial, rng: (True, False)))
    shape = SHAPES['clear_win' if kind in ('mixed_unknowns', 'critical_veto') else kind]
    spec = plan(shape[0], draws=draws, repeats=REPEATS, **options)
    rows = trial_records(spec, lambda trial, rng: outcome_pair(rng, *shape[1:]), rng=random.Random(seed),
                         unknown=.15 if kind == 'mixed_unknowns' else 0, critical=kind == 'critical_veto')
    return dict(spec=spec, records=rows)


GOLDEN_KINDS = ('supported', 'inconclusive', 'regression', 'rejected_critical', 'incomplete')


def golden_case(kind, *, declared=False):
    """A small deterministic bank (no random draws) whose fixed analysis is stored in golden_profiles.

    The declared and undeclared plans differ only by the ``bayes_decision`` field, so their Hoeffding analyses must match.
    """
    spec = plan(12, min_roots=2, draws=2000, bayes=None if declared else False, precision_half_width=.2)
    wins = {'supported': lambda i: (True, False), 'regression': lambda i: (False, True),
            'inconclusive': lambda i: (True, False) if i % 2 else (True, True)}.get(kind, lambda i: (True, False))
    rows = trial_records(spec, lambda trial, rng: wins(int(trial['root'].rsplit('r', 1)[1])))
    if kind == 'rejected_critical':
        rows[0] = replace(rows[0], critical_regression=True)
    if kind == 'incomplete':
        rows[0] = replace(rows[0], outcome=None, status='infrastructure_error')
    return dict(spec=spec, records=rows)


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as exit_info:
            code = exit_info.code
    return code, out.getvalue(), err.getvalue()


def experiment(tmp_path, spec, rows):
    """Run ``ahl experiment --records`` on a plan and its records; return (exit code, raw stdout, stderr)."""
    (tmp_path / 'plan.json').write_text(json.dumps(spec.to_dict()))
    (tmp_path / 'trials.json').write_text(json.dumps([asdict(row) for row in rows]))
    return run('experiment', '--spec', str(tmp_path / 'plan.json'), '--records', str(tmp_path / 'trials.json'))
