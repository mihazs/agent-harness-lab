"""Roll saved evaluator trials up into one private skill measurement report.

The report keeps staged change, availability, observed activation, shadowing, routing and graded outcome as
separate counts. It never crosses activation with outcome and carries no utility estimate: that needs a frozen
paired experiment (`evaluation.skills.paired_transfer`)."""
import argparse
import json
from pathlib import Path

from .skills import ACTIVATION_RECEIPT_PROFILE, shadowing_state

PROFILE = 'ahl-skill-measurement-report-v1'
ARMS = ('baseline', 'candidate')
ACTIVATION = ('activated', 'not_observed', 'unknown')
SHADOWING = ('ambient_same_name', 'none_found', 'unknown')
ROUTING = ('natural', 'preload', 'unrecorded')
MAX_RESULTS_BYTES = 64 * 1024 * 1024


def _rows(value):
    """Availability rows that name a skill and a known placement state, else None (not recorded)."""
    if not isinstance(value, list) or any(
            not isinstance(row, dict) or not isinstance(row.get('name'), str) or row.get('state') not in ('present', 'absent')
            or not isinstance(row.get('bundle_sha256'), (str, type(None))) for row in value):
        return None
    return value


def _activation(receipt):
    """A receipt state counts only when its own evidence supports it; anything else is unknown."""
    if not isinstance(receipt, dict) or receipt.get('profile') != ACTIVATION_RECEIPT_PROFILE:
        return 'unknown'
    state = receipt.get('state')
    if state == 'activated' and receipt.get('signals'):
        return 'activated'
    if state == 'not_observed' and receipt.get('skill_id'):
        return 'not_observed'
    return 'unknown'


def _shadowing(record, rows):
    found = record.get('skill_shadowing')
    if (not isinstance(found, list) or any(not isinstance(row, dict) or not isinstance(row.get('name'), str)
                                           or row.get('state') not in SHADOWING for row in found)
            or {row['name'] for row in found} != {row['name'] for row in rows}):
        return 'unknown'
    return shadowing_state(found)


def _cell():
    return dict(trials=0, activation=dict.fromkeys(ACTIVATION, 0), outcomes=dict(passed=0, failed=0, ungraded=0))


def _count(cell, record):
    cell['trials'] += 1
    cell['activation'][_activation(record.get('skill_activation_receipt'))] += 1
    status = record.get('status')
    cell['outcomes'][status if status in ('passed', 'failed') else 'ungraded'] += 1


def _view(seen):
    """One arm's state for a skill: present or absent with a bundle hash, not_recorded or inconsistent."""
    if not seen:
        return 'not_recorded', None
    if len(seen) > 1:
        return 'inconsistent', None
    (state, digest), = seen
    return state, digest


def _change(baseline, candidate):
    (before, old), (after, new) = baseline, candidate
    if before in ('not_recorded', 'inconsistent') or after in ('not_recorded', 'inconsistent'):
        return 'unknown'
    if before == after == 'absent':
        return 'absent_in_both'
    if before == after:
        return 'unchanged' if old == new else 'updated'
    return 'created' if after == 'present' else 'removed'


def skill_report(results):
    """Roll up the `trials` of an evaluator results mapping; the input is never modified."""
    trials = results.get('trials') if isinstance(results, dict) else None
    if not isinstance(trials, list):
        raise ValueError('results must be an object with a trials list')
    arms, staged = {}, {}
    for record in trials:
        if not isinstance(record, dict):
            raise ValueError('every trial must be an object')
        version = record.get('version')
        if version not in ARMS:
            raise ValueError('every trial needs a version of baseline or candidate')
        arm = arms.setdefault(version, dict(
            overall=_cell(), availability=dict(present=0, absent=0, none=0, unrecorded=0),
            shadowing=dict.fromkeys(SHADOWING, 0), routing={name: _cell() for name in ROUTING}))
        _count(arm['overall'], record)
        rows = _rows(record.get('skill_availability'))
        if rows is None:
            arm['availability']['unrecorded'] += 1
            continue
        for row in rows:
            staged.setdefault(row['name'], {}).setdefault(version, set()).add((row['state'], row.get('bundle_sha256')))
        if not rows:
            arm['availability']['none'] += 1
            continue
        present = any(row['state'] == 'present' for row in rows)
        arm['availability']['present' if present else 'absent'] += 1
        arm['shadowing'][_shadowing(record, rows)] += 1
        if present:
            preload = record.get('skill_preload_requested')
            _count(arm['routing']['preload' if preload is True else 'natural' if preload is False else 'unrecorded'], record)
    skills = []
    for name in sorted(staged):
        (before, old), (after, new) = (_view(staged[name].get(version)) for version in ARMS)
        skills.append(dict(name=name, baseline=before, candidate=after, baseline_bundle_sha256=old, candidate_bundle_sha256=new,
                           change=_change((before, old), (after, new))))
    return dict(profile=PROFILE, estimand='skill_availability_intent_to_treat', causal_effect='requires_frozen_paired_experiment',
                arms=arms, staged_skills=skills, publication_allowed=False)


def report_main(argv=None):
    from .integrity import read_regular
    parser = argparse.ArgumentParser(prog='ahl skills report',
        description='Roll the skill availability, activation, shadowing, routing and graded outcome fields of a saved '
                    'evaluator results.json into one private report; it writes nothing and estimates no utility.')
    parser.add_argument('--results', required=True, type=Path,
                        help='Private evaluator results.json: a regular file, at most 64 MiB, with no symlink in its path')
    args = parser.parse_args(argv)
    try:
        report = skill_report(json.loads(read_regular(args.results, MAX_RESULTS_BYTES)))
    except (ValueError, OSError) as error:
        parser.error(f'results: {error}')
    print(json.dumps(report, sort_keys=True))
    return 0
