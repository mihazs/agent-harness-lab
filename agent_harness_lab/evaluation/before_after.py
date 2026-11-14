"""Before/after guard: one paired comparison of a baseline and a candidate on identical frozen inputs (#804).

Observed patterns only: a verdict never grants recommendation, adoption, promotion or publication authority. An outcome
that is not an explicit pass or fail is unknown and never counted as either, and an unknown identity field is never read
as a match. The paired unit is the root (the task unless declared), so repeats inside a root are never treated as
independent. ``candidate_regressed`` blocks adoption on the observed paired delta alone, so a small bank cannot hide a
loss; ``improved`` also needs the interval to clear the margin, so a gain inside noise stays ``inconclusive``.
"""
from fractions import Fraction
import hashlib
import math
from pathlib import Path
import re

from agent_harness_lab.sources.schema import digest


PROFILE = 'ahl-before-after-guard-v1'
INVESTIGATION_PROFILE = 'ahl-regression-investigation-v1'
VERDICTS = ('candidate_regressed', 'improved', 'inconclusive')
CAUSES = ('candidate_caused', 'pipeline_caused', 'undetermined')
IDENTITY_FIELDS = ('bank_sha256', 'grader_sha256', 'seed_schedule_sha256', 'budget_share', 'runtime_sha256')
DEFAULT_MARGIN, DEFAULT_ALPHA = 0.0, 0.05
NEXT_STEP = {
    'candidate_caused': 'Read the flipped trials, then revise or discard the candidate; the frozen inputs were identical, so this is a worse harness change and not an AHL defect.',
    'pipeline_caused': 'Compare the frozen inputs identity of both arms, fix the pipeline defect that made them differ, add a regression test for it, then rerun the evaluation.',
    'undetermined': 'Establish the unknown frozen-input facts before treating the regression as caused by the candidate, then rerun the evaluation.',
}
_HEX = re.compile(r'[0-9a-f]{64}')
_SHA_FIELDS = tuple(name for name in IDENTITY_FIELDS if name.endswith('_sha256'))


def _bool_or_none(value, name):
    if value is not None and type(value) is not bool: raise ValueError(f'{name} must be true, false or null')
    return value


def schedule_sha256(tasks, repeats=1, seed=None):
    """Identity of the planned trial schedule: ordered task ids, repeats per task and arm, and the declared seed."""
    return digest(dict(tasks=[task['id'] for task in tasks], repeats=repeats, seed=seed))


def launch_sha256(agent, executable, inherit_env=False):
    """Identity of the launch configuration both arms shared; it binds neither the served model nor the runtime build."""
    return digest(dict(profile='ahl-launch-configuration-v1', agent=agent, executable=str(Path(executable).resolve()),
                       inherit_env=bool(inherit_env)))


def frozen_inputs(**fields):
    """The identity one arm ran under; a field the controller could not observe is None, which is unknown."""
    if set(fields) - set(IDENTITY_FIELDS): raise ValueError('unknown frozen-input field')
    value = {name: fields.get(name) for name in IDENTITY_FIELDS}
    for name in _SHA_FIELDS:
        if value[name] is not None and not (isinstance(value[name], str) and _HEX.fullmatch(value[name])):
            raise ValueError(f'{name} must be a lowercase SHA-256 or null')
    if value['budget_share'] is not None and not isinstance(value['budget_share'], dict):
        raise ValueError('budget_share must be an object or null')
    return dict(value, identity_sha256=digest(value))


def compare_identities(baseline, candidate):
    """identical (every field known and equal), mismatch (a field known on both arms differs) or unverified (a field is unknown)."""
    mismatched = [name for name in IDENTITY_FIELDS
                  if baseline.get(name) is not None and candidate.get(name) is not None and baseline[name] != candidate[name]]
    unknown = [name for name in IDENTITY_FIELDS if baseline.get(name) is None or candidate.get(name) is None]
    return dict(status='mismatch' if mismatched else 'unverified' if unknown else 'identical',
                mismatched=mismatched, unknown=[name for name in unknown if name not in mismatched])


def _rows(pairs):
    """Validated rows keyed by (task, repeat); outcomes are True, False or None and nothing else."""
    rows, seen = [], set()
    for pair in pairs:
        task, repeat = pair['task'], pair.get('repeat', 0)
        if not isinstance(task, str) or not task or type(repeat) is not int or (task, repeat) in seen:
            raise ValueError('each pair needs a unique task and repeat')
        seen.add((task, repeat))
        areas = pair.get('areas')
        if areas is not None and not (isinstance(areas, (list, tuple)) and all(isinstance(area, str) and area for area in areas)):
            raise ValueError('areas must be a list of names or null')
        rows.append(dict(task=task, root=pair.get('root') or task, repeat=repeat, split=pair.get('split'),
                         areas=sorted(set(areas)) if areas else [],
                         baseline=_bool_or_none(pair['baseline'], 'baseline'), candidate=_bool_or_none(pair['candidate'], 'candidate')))
    return rows


def _known(rows):
    return [row for row in rows if row['baseline'] is not None and row['candidate'] is not None]


def _roots(rows):
    """Per root: the mean baseline score, candidate score and paired difference, exactly."""
    grouped = {}
    for row in rows: grouped.setdefault(row['root'], []).append(row)
    return {root: (Fraction(sum(r['baseline'] for r in group), len(group)), Fraction(sum(r['candidate'] for r in group), len(group)))
            for root, group in grouped.items()}


def _interval(mean, count, alpha, span):
    from .statistics import radius  # lazy: the statistics module is heavier than this guard
    half = radius([1 / count] * count, alpha) * span / 2
    return dict(mean=mean, low=max(mean - half, -1 if span == 2 else 0), high=min(mean + half, 1))


def _flips(rows):
    return (sum(r['baseline'] and not r['candidate'] for r in rows), sum(r['candidate'] and not r['baseline'] for r in rows))


def decide(pairs, *, baseline, candidate, margin=DEFAULT_MARGIN, alpha=DEFAULT_ALPHA):
    """The receipt block: both scores with intervals, the paired delta, the frozen identity of each arm and a verdict.

    ``pairs`` hold ``task``, ``baseline`` and ``candidate`` outcomes (True, False or None) and optionally ``root``, ``repeat``,
    ``areas`` and ``split``. ``baseline`` and ``candidate`` are ``frozen_inputs`` identities.
    """
    if type(margin) not in (int, float) or not math.isfinite(margin) or not 0 <= margin < 1: raise ValueError('margin must be in [0, 1)')
    if type(alpha) not in (int, float) or not 0 < alpha < 1: raise ValueError('alpha must be in (0, 1)')
    rows = _rows(pairs)
    eligible = _known(rows)
    comparison = compare_identities(baseline, candidate)
    roots = _roots(eligible)
    flipped_to_fail, flipped_to_pass = _flips(eligible)
    pair_counts = dict(eligible=len(eligible), unknown=len(rows) - len(eligible), flipped_to_fail=flipped_to_fail,
                       flipped_to_pass=flipped_to_pass, unchanged=len(eligible) - flipped_to_fail - flipped_to_pass,
                       repeats_per_task_arm=max([sum(r['task'] == task for r in rows) for task in {r['task'] for r in rows}], default=0))
    frozen = dict(baseline=baseline, candidate=candidate, comparison=comparison)
    block = dict(profile=PROFILE, claim='observed_pattern', margin=margin, alpha=alpha, unit='root', independence='unverified',
                 pairs=pair_counts, frozen_inputs=frozen, scores=None, delta=None, adoption_authority=False,
                 automatic_promotion=False, publication_allowed=False)
    if not roots:
        return dict(block, verdict='inconclusive', reasons=['no_paired_outcomes'], adoption_blocked=False)
    count = len(roots)
    base_mean, cand_mean = (sum(values[i] for values in roots.values()) / count for i in (0, 1))
    delta = float(cand_mean - base_mean)
    interval = _interval(delta, count, alpha, 2)
    regressed = delta < -margin
    reasons = []
    if pair_counts['unknown']: reasons.append('unknown_outcomes')
    if comparison['mismatched']: reasons.append('frozen_inputs_mismatch')
    elif comparison['unknown']: reasons.append('frozen_inputs_unverified')
    if not interval['low'] > margin: reasons.append('interval_not_above_margin')
    verdict = 'candidate_regressed' if regressed else 'improved' if not reasons else 'inconclusive'
    return dict(block, verdict=verdict, adoption_blocked=regressed,
                reasons=['delta_below_negative_margin'] if regressed else ['interval_above_margin'] if not reasons else reasons,
                scores=dict(baseline=_interval(float(base_mean), count, alpha, 1), candidate=_interval(float(cand_mean), count, alpha, 1)),
                delta=dict(interval, method='weighted_root_Hoeffding', interval_excludes_margin=bool(interval['high'] < -margin)))


def _state(row, arm):
    return {True: 'passed', False: 'failed', None: 'unknown'}[row[arm]]


def _changes(comparison, grader_changed, inputs_unchanged):
    mismatched, unknown = set(comparison['mismatched']), set(comparison['unknown'])
    shared = {'bank_sha256', 'seed_schedule_sha256'}
    grader = ('changed' if grader_changed is True or 'grader_sha256' in mismatched
              else 'unchanged' if grader_changed is False and 'grader_sha256' not in unknown else 'unknown')
    inputs = ('changed' if inputs_unchanged is False or mismatched & shared
              else 'unchanged' if inputs_unchanged is True and not unknown & shared else 'unknown')
    budget = 'differed' if 'budget_share' in mismatched else 'unknown' if 'budget_share' in unknown else 'identical'
    return dict(grader=grader, frozen_inputs=inputs, budget=budget)


def investigate(pairs, guard, *, grader_changed=None, inputs_unchanged=None):
    """Per-task and per-area deltas, the trials that flipped, what changed between the arms and a classified cause."""
    rows = _rows(pairs)
    eligible = _known(rows)
    comparison = guard['frozen_inputs']['comparison']
    changes = _changes(comparison, _bool_or_none(grader_changed, 'grader_changed'), _bool_or_none(inputs_unchanged, 'inputs_unchanged'))
    if comparison['mismatched'] or {'changed', 'differed'} & set(changes.values()): cause, basis = 'pipeline_caused', sorted(
        [f'{name}_differed' for name in comparison['mismatched']] + [f'{name}_{value}' for name, value in changes.items() if value in ('changed', 'differed')])
    elif comparison['unknown'] or 'unknown' in changes.values(): cause, basis = 'undetermined', sorted(
        [f'{name}_unknown' for name in comparison['unknown']] + [f'{name}_unknown' for name, value in changes.items() if value == 'unknown'])
    else: cause, basis = 'candidate_caused', ['frozen_inputs_identical', 'grader_unchanged', 'budget_identical']
    areas = {}
    for row in eligible:
        for area in row['areas'] or ['untagged']: areas.setdefault(area, []).append(row)
    def flipped(kind): return [dict(task=r['task'], root=r['root'], repeat=r['repeat'], split=r['split'], areas=r['areas'])
                               for r in eligible if (r['baseline'] and not r['candidate'] if kind == 'fail' else r['candidate'] and not r['baseline'])]
    def area_delta(group):
        roots = _roots(group)
        return float(sum(c - b for b, c in roots.values()) / len(roots))
    return dict(profile=INVESTIGATION_PROFILE, verdict=guard['verdict'], margin=guard['margin'], delta=guard['delta'],
                per_task=[dict(task=r['task'], root=r['root'], repeat=r['repeat'], split=r['split'], areas=r['areas'],
                               baseline=_state(r, 'baseline'), candidate=_state(r, 'candidate'),
                               delta=None if r['baseline'] is None or r['candidate'] is None else int(r['candidate']) - int(r['baseline']))
                          for r in rows],
                per_area={area: dict(pairs=len(group), delta=area_delta(group), flipped_to_fail=_flips(group)[0], flipped_to_pass=_flips(group)[1])
                          for area, group in sorted(areas.items())},
                flipped=dict(to_fail=flipped('fail'), to_pass=flipped('pass')), changes=changes, comparison=comparison,
                cause={'class': cause, 'basis': basis,
                       'caveats': ['single_trial_per_arm_not_separated_from_run_to_run_variance'] if guard['pairs']['repeats_per_task_arm'] <= 1 else []},
                next_step=NEXT_STEP[cause], adoption_authority=False, automatic_promotion=False, publication_allowed=False)


def run_pairs(manifest, trials):
    """Pairs of one covered single-run evaluation; a trial whose split disagrees with the manifest is unknown evidence."""
    declared = {task['id']: task.get('split') for task in manifest['tasks']}
    outcome = {(trial['version'], trial['task']): {'passed': True, 'failed': False}.get(trial.get('status'))
               if trial.get('split') == declared[trial['task']] else None for trial in trials}
    return [dict(task=task['id'], split=task.get('split'), root=task.get('root') if isinstance(task.get('root'), str) else None,
                 areas=task.get('areas') if isinstance(task.get('areas'), list) else None,
                 baseline=outcome.get(('baseline', task['id'])), candidate=outcome.get(('candidate', task['id'])))
            for task in manifest['tasks']]


def attach(result, output, run, *, margin=DEFAULT_MARGIN):
    """Add the guard block to the receipt of a covered single-run evaluation.

    A candidate that regressed on the final attempt and ended as the bare ``evaluation_failed`` becomes ``blocked`` with reason
    ``candidate_regressed`` and an ``investigation.json`` beside the receipt. Any failure leaves the run result unchanged.
    """
    from agent_harness_lab.monitoring.state import atomic_json
    try:
        pairs = run_pairs(run['manifest'], run['trials'])
        arm = frozen_inputs(**run['frozen'])
        guard = dict(decide(pairs, baseline=arm, candidate=dict(arm), margin=margin), bindings=dict(run['bindings']))
        update = dict(before_after=guard)
        if guard['adoption_blocked'] and run['bindings']['describes_final_attempt'] and result.get('status') == 'evaluation_failed':
            artifact = investigate(pairs, guard, grader_changed=False, inputs_unchanged=run['inputs_unchanged'])
            path = Path(output) / 'investigation.json'
            atomic_json(path, artifact)
            update.update(status='blocked', reason='candidate_regressed', investigation=dict(
                path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), cause=artifact['cause']['class']))
        result.update(update)
    except Exception as exc:  # An advisory guard never changes the run result.
        result['before_after'] = dict(status='unavailable', reason=type(exc).__name__, adoption_authority=False)
