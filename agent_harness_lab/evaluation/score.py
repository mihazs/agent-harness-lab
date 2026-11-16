"""`ahl score`: a read-only per-area report over a bank and the recorded runs of one paired experiment (#797).

Each declared capability area gets one row: its tasks, distinct roots, the success rate of each recorded arm with its
interval, and, when a candidate was recorded, the paired delta and critical regressions. The numbers come from the existing
`statistics.analyze` (run once per area on that area's tasks) and the loop's `absolute_score`; nothing here adds a
statistic. Every row carries one evidence state, and an unknown outcome is never read as a pass, a fail or independence:

- scored: every outcome known, at least the plan's minimum of roots, and every task reviewed as independent;
- provisional: an interval is shown but no verdict, because an outcome is unknown or the roots are below the minimum;
- no_evidence: no experiment task carries the area, or none of its outcomes is graded; a number would be a zero;
- blocked: the bank or a grader is not qualified, or the experiment design cannot be scored per area; no number is shown.

Recorded sessions (#799) are optional, separate context: `session_areas` places each session by content-free signals in an
`advisory_sessions` section. It is advisory and observed-only, never a state above, and it leaves every row and the overall figure as is.
"""
import argparse
from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

from . import integrity, session_areas
from .manifest_contract import ContractError
from .plan import ExperimentSpec, schedule
from .statistics import TrialRecord, analyze
from .task_bank import inspect

PROFILE = 'ahl-score-v1'
MAX_INPUT_BYTES = 16 * 1024 * 1024
ARMS = ('baseline', 'candidate')


def _outcome(record):
    """passed or failed only for a graded executable-grader record with a boolean outcome; anything else is unknown."""
    if record.status == 'graded' and record.provenance == 'executable_grader' and type(record.outcome) is bool:
        return 'passed' if record.outcome else 'failed'
    return 'unknown'


def _area_spec(spec, chosen):
    """The frozen plan restricted to the chosen tasks: family weights renormalized, fixed-analysis fields only."""
    weights = {family: weight for family, weight in spec.family_weights if family in {task[2] for task in chosen}}
    total = sum(weights.values())
    return replace(spec, tasks=tuple(chosen), max_trials=2 * len(chosen) * spec.repeats, bayes_decision=None,
                   resource_contract_sha256=None, resource_quality_policy_sha256=None,
                   family_weights=tuple((family, weight / total) for family, weight in weights.items()))


def _interval(score):
    """One arm's success rate, or None when no outcome is known: that would be a zero, not a measurement."""
    if score['evidence'] == 'no_evidence': return None
    return dict(success_rate=score['point'], lower=score['lower'], upper=score['upper'], independent_roots=score['roots'],
                unknown_roots=score['unknown_roots'], evidence=score['evidence'])


def _measure(spec, chosen, plan, usable, candidate):
    """Success rate per recorded arm and the paired analysis of the chosen tasks, from the full plan's usable records."""
    from agent_harness_lab.loop.adapters import absolute_score
    area = _area_spec(spec, chosen)
    own = {(t['task'], t['repetition'], t['version']): t['trial_id'] for t in plan}
    roots = {task: root for task, root, _ in chosen}
    arms, kept, found = ARMS if candidate else ARMS[:1], [], {}
    for trial in schedule(area):
        record = usable.get(own[trial['task'], trial['repetition'], trial['version']])
        if record is None: continue
        kept.append(replace(record, trial_id=trial['trial_id']))
        found.setdefault(trial['version'], []).append(dict(task=f"{trial['task']}#{trial['repetition']}", status=_outcome(record)))
    units = [(f'{task}#{n}', roots[task]) for task in roots for n in range(spec.repeats)]  # a repetition is a unit of its root
    scores = {arm: absolute_score(units, found.get(arm, []), alpha=spec.alpha, min_roots=spec.min_roots) for arm in arms}
    foreign = any((r.baseline_hash, r.candidate_hash, r.grader_hash, r.runtime_hash) !=
                  (spec.baseline_hash, spec.candidate_hash, spec.grader_hash, spec.runtime_hash) for r in kept)
    # With a candidate the fixed analysis checks provenance; a baseline-only record set must be checked here.
    paired = analyze(area, kept) if candidate else dict(status='blocked', reason='provenance_mismatch') if foreign else None
    critical = sum(r.status == 'graded' and r.provenance == 'executable_grader' and r.critical_regression is True for r in kept)
    return scores, paired, critical


def _row(spec, chosen, plan, usable, candidate, flagged, design):
    """The counts, state and numbers of one task set; blocked and no_evidence rows carry no number."""
    row = dict(tasks=len(chosen), independent_roots=len({root for _, root, _ in chosen}), state='no_evidence',
               reason='no_experiment_task', success=None, paired=None, critical_regressions=None)
    if not chosen: return row
    if design: return dict(row, state='blocked', reason='experiment_design_unsupported')
    if flagged: return dict(row, state='blocked', reason='bank_not_qualified')
    scores, paired, critical = _measure(spec, chosen, plan, usable, candidate)
    if paired is not None and paired['status'] == 'blocked': return dict(row, state='blocked', reason=paired['reason'])
    evidence = [score['evidence'] for score in scores.values()]
    state, reason = 'scored', None
    if set(evidence) == {'no_evidence'}: state, reason = 'no_evidence', 'no_graded_outcome'
    elif set(evidence) != {'scored'} or paired is not None and paired['status'] == 'incomplete':
        state, reason = 'provisional', 'unknown_outcomes' if any(s['unknown_roots'] for s in scores.values()) else 'below_minimum_roots'
    elif paired is not None and paired.get('reason') == 'insufficient_independent_roots':
        state, reason = 'provisional', 'below_minimum_roots'
    if state == 'no_evidence': return dict(row, state=state, reason=reason)
    primary = (paired or {}).get('primary')
    verdict = None if paired is None else dict(status=paired['status'], reason=paired['reason'], **(
        dict(delta=primary['mean'], lower=primary['low'], upper=primary['high'], independent_roots=primary['independent_roots'])
        if primary else {}))
    return dict(row, state=state, reason=reason, success={arm: _interval(score) for arm, score in scores.items()},
                paired=verdict, critical_regressions=None if paired is None else critical)


def report(registry, bank, issues, spec, records, areas=(), sessions=None):
    """The per-area report of one experiment: `registry` the ahl.json areas, `bank` its task declarations, `issues` the
    bank inspection issues, `spec` the ExperimentSpec, `records` the TrialRecord rows, `areas` the ids to narrow to and
    `sessions` optional parsed session signal summaries, placed as advisory context apart from the scored rows."""
    spec.validate()
    declared = {entry['id']: entry for entry in registry}
    for index, area in enumerate(areas):
        if area in areas[:index]: raise ContractError('area_duplicate', 'score_selection', f'--area[{index}]', 'ahl.json')
        if area not in declared: raise ContractError('score_area_undeclared', 'score_selection', f'--area[{index}]', 'ahl.json')
    known = {task['task_id']: task for task in bank}
    for task, root, family in spec.tasks:
        if task in known and (known[task]['root_id'], known[task]['family']) != (root, family):
            raise ValueError('an experiment task does not match the root or family its bank declaration gives')
    tagged = {task: tuple(known[task].get('areas', ())) if task in known else () for task, _, _ in spec.tasks}
    design = spec.sequential is not None or spec.cohort_manifest_sha256 is not None
    plan = [] if design else schedule(spec)
    planned, counts = {trial['trial_id'] for trial in plan}, Counter(record.trial_id for record in records)
    usable = {record.trial_id: record for record in records if record.trial_id in planned and counts[record.trial_id] == 1}  # a repeated id is unknown
    candidate = any(trial['version'] == 'candidate' and trial['trial_id'] in usable for trial in plan)
    unqualified = {name for issue in issues for name in ([issue['task']] if 'task' in issue else issue.get('tasks') or [])}
    bankwide = any('task' not in issue and 'tasks' not in issue for issue in issues)
    def one(chosen):
        return _row(spec, chosen, plan, usable, candidate, bankwide or any(task in unqualified for task, _, _ in chosen), design)
    rows = []
    for area in declared:
        chosen = [task for task in spec.tasks if area in tagged[task[0]]]
        rows.append(dict(id=area, label=declared[area]['label'], bank_tasks=sum(area in task.get('areas', ()) for task in bank),
                         **one(chosen)))
    withheld = ('area_selection' if areas else 'no_declared_area' if not rows
                else 'not_every_area_scored' if any(row['state'] != 'scored' for row in rows) else None)
    result = dict(profile=PROFILE, experiment=dict(id=spec.experiment_id, profile=spec.profile, partition=spec.partition,
                                                   alpha=spec.alpha, min_roots=spec.min_roots, repeats=spec.repeats),
                  arms=list(ARMS if candidate else ARMS[:1]), trials=dict(planned=len(plan), recorded=len(usable)),
                  untagged_tasks=sum(not tags for tags in tagged.values()),
                  areas=[row for row in rows if not areas or row['id'] in areas],
                  overall=one(list(spec.tasks)) if withheld is None else None, overall_withheld=withheld)
    if sessions is not None: result['advisory_sessions'] = session_areas.advisory(registry, sessions, areas)
    return result


def _number(value):
    return 'n/a' if value is None else f'{value:.3f}'


def _success(entry):
    if entry is None: return 'n/a'
    return f"{_number(entry['success_rate'])} [{_number(entry['lower'])}, {_number(entry['upper'])}]"


def _cells(row):
    success, paired = row['success'] or {}, row['paired'] or {}
    delta = 'n/a' if 'delta' not in paired else f"{_number(paired['delta'])} [{_number(paired['lower'])}, {_number(paired['upper'])}]"
    return [row['state'], row['reason'] or '', str(row['tasks']), str(row['independent_roots']), _success(success.get('baseline')),
            _success(success.get('candidate')), delta, paired.get('status', 'n/a'),
            'n/a' if row['critical_regressions'] is None else str(row['critical_regressions'])]


def markdown(result):
    """The report for people; deterministic, one table row per declared area."""
    experiment = result['experiment']
    lines = [f"# Per-area harness score: {experiment['id']}", '',
             f"Recorded arms: {', '.join(result['arms'])}. Trials recorded {result['trials']['recorded']} of {result['trials']['planned']}. "
             f"Interval alpha {experiment['alpha']}, minimum roots {experiment['min_roots']}. Untagged experiment tasks: {result['untagged_tasks']}.", '']
    head = ['Area', 'State', 'Reason', 'Tasks', 'Roots', 'Baseline success [interval]', 'Candidate success [interval]',
            'Paired delta [interval]', 'Paired status', 'Critical regressions']
    if result['areas']:
        lines += ['| ' + ' | '.join(head) + ' |', '|' + ' --- |' * len(head)]
        for row in result['areas']:
            label = row['label'].replace('|', '\\|')
            lines.append('| ' + ' | '.join([f"{label} (`{row['id']}`)", *_cells(row)]) + ' |')
    else:
        lines.append('No capability area is declared in ahl.json.')
    overall = result['overall']
    lines += ['', 'Overall: ' + (f"{_cells(overall)[4]} baseline, paired delta {_cells(overall)[6]} (pooled over every experiment task)."
                                 if overall else f"withheld ({result['overall_withheld']}); it is shown only when every declared area is scored."),
              '', 'A task in several areas counts in each. No area is averaged into another, and intervals are not corrected across areas.']
    return '\n'.join(lines) + '\n' + (session_areas.markdown(result['advisory_sessions']) if 'advisory_sessions' in result else '')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ahl score', description='Report the result of one recorded paired experiment per declared capability area, '
        'each with an evidence state (scored, provisional, no_evidence, blocked). Read-only: no model call, no network, no file written.',
        epilog='Reads PROJECT/ahl.json (areas and bank) and the experiment files that `ahl experiment` takes. Output is JSON (default) or Markdown on stdout. '
               'Exit: 0 report printed; 2 invalid input, an invalid --sessions file or an undeclared --area. Example: ahl score . --spec experiment.json --records trials.json --area coding')
    parser.add_argument('project', nargs='?', type=Path, default=Path.cwd(), help='Project directory whose ahl.json declares the areas and the bank (default: current directory)')
    parser.add_argument('--spec', type=Path, required=True, help='Existing JSON ExperimentSpec of the recorded paired experiment; a sequential or cohort plan is reported as blocked')
    parser.add_argument('--records', type=Path, required=True, help='Existing JSON array of the TrialRecord rows recorded for that spec; a missing or repeated trial is unknown')
    parser.add_argument('--area', action='append', default=[], help='Declared area id to report; repeat for several (default: every declared area); an undeclared id is an error')
    parser.add_argument('--sessions', type=Path, help='Existing JSON array of content-free session signal summaries (session id, tool, extension and command-family counts); '
                        'adds an advisory, observed-only placement of each session beside the rows, never a scored state')
    parser.add_argument('--format', choices=('json', 'md'), default='json', help='Output format on stdout (default: %(default)s)')
    args = parser.parse_args(argv)
    try:
        from agent_harness_lab import config
        settings = config.load(args.project / 'ahl.json')[0]
        registry = settings.get('areas', [])
        path = config.bank_path(args.project, settings)
        reviewed = inspect(path, registry=registry)
        raw = integrity.read_regular(path, None)
        if reviewed['bank_sha256'] != settings['bank']['sha256'] or reviewed['bank_sha256'] != hashlib.sha256(raw).hexdigest():
            raise ValueError('the configured bank digest does not match the bank')
        spec = ExperimentSpec.parse(json.loads(integrity.read_regular(args.spec, MAX_INPUT_BYTES)))
        rows = json.loads(integrity.read_regular(args.records, MAX_INPUT_BYTES))
        if not isinstance(rows, list): raise ValueError('records must be a JSON array')
        result = report(registry, json.loads(raw)['tasks'], reviewed['issues'], spec, [TrialRecord(**row) for row in rows], tuple(args.area),
                        None if args.sessions is None else session_areas.parse(json.loads(integrity.read_regular(args.sessions, MAX_INPUT_BYTES))))
        sys.stdout.write(markdown(result) if args.format == 'md' else json.dumps(result, indent=2, allow_nan=False) + '\n')
    except (ValueError, OSError, TypeError, KeyError) as error:
        print(f'ahl score: {error}', file=sys.stderr)
        return 2
    return 0
