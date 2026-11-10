"""Frozen drift plans and receipts that keep capture, task-mix and verified-performance drift apart.

A ``drift-plan-v1`` freezes, before any monitored window is read, the reference
revisions, stream, strata, observable task features, the independent task-root
unit, the capture eligibility rule, a non-overlapping fixed-window schedule,
minimum exposure, effects of interest and the per-channel alert budget.
``freeze_reference`` summarizes the reference window as counts only, and
``assess`` returns a ``drift-receipt-v1`` for one later window.

The capture channel raises deterministic operational alerts (schema mismatch,
conflicting duplicates, missing or non-integer cursors, cursor gaps or collisions,
untimed runs and coverage below the frozen rule). A run with partial or unknown
capture, a missing required field, truncation, parse rejection, or unknown task
root or harness identity is ineligible: it lowers capture coverage and never enters the task-mix
or performance denominators. Retries of one task root count once. The task-mix
and performance channels report bounded descriptive exposure only; their
statistical detectors are not implemented yet, so they always return
``insufficient_evidence``. No receipt sets a regression label, establishes a
cause, or grants promotion or rollback authority. Pure: nothing is read,
written or charged.
"""
import hashlib
import json
import math
import re

from agent_harness_lab.diagnostics.coverage import CAPTURE_STATES
from agent_harness_lab.diagnostics.triage_policy import _number, _sha, policy_sha256, root_sha256

PLAN_PROFILE = 'drift-plan-v1'
REFERENCE_PROFILE = 'drift-reference-v1'
RECEIPT_PROFILE = 'drift-receipt-v1'
STATES = ('alert', 'no_alert', 'insufficient_evidence', 'evidence_of_noninferiority')
STRATUM_FIELDS = ('task_family', 'runtime', 'effective_model_version')
VERIFIED_PROVENANCE = ('executable_grader', 'independent_review')
ACTIONS = ('observe', 'review')
STATISTICAL_CHANNELS = ('task_mix', 'performance')
UNKNOWN = 'unknown'
OTHER = 'other'
MAX_CATEGORIES = 64
MAX_INPUT_RUNS = 100000
UNKNOWN_IDENTITIES = frozenset(('unknown', 'unverified', 'unavailable', 'unspecified'))
RESERVED = frozenset(('run_id', 'task_root', 'cursor', 'started', 'schema_version', 'capture', 'truncated',
                      'parse_rejected', 'harness_revision', 'outcome', 'outcome_provenance',
                      'requested_model', *STRATUM_FIELDS))
PLAN_FIELDS = frozenset(('schema_version', 'profile', 'stream_sha256', 'reference_revisions_sha256',
                         'source_schema_version', 'unit', 'strata', 'features', 'required_fields', 'capture',
                         'method', 'reference_window', 'window_length', 'max_windows', 'min_exposure',
                         'effect_size', 'alert_budget', 'multiplicity', 'reset_policy', 'action'))
EFFECTS = ('task_mix_total_variation', 'failure_rate_increase')
LIMITATIONS = ('channel_alerts_are_not_causal_regressions', 'statistical_detectors_not_implemented',
               'root_independence_unverified', 'label_authority_unverified')
_NAME = re.compile(r'[a-z][a-z0-9_]{0,63}')
_ALIAS = 'requested model aliases cannot become precise strata or features'


def _names(value, what, *, allow_empty=True, allowed=None):
    if not isinstance(value, list) or (not value and not allow_empty) or len(set(value)) != len(value):
        raise ValueError(f'{what} must be a list of unique field names')
    for name in value:
        if name == 'requested_model':
            raise ValueError(_ALIAS)
        if not isinstance(name, str) or not _NAME.fullmatch(name) or (allowed and name not in allowed):
            raise ValueError(f'invalid {what} field name')
    return value


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _positive_int(value):
    return type(value) is int and value > 0


def check_plan(plan):
    """Validate a frozen ``drift-plan-v1`` and return its SHA-256."""
    if not isinstance(plan, dict) or set(plan) != PLAN_FIELDS:
        raise ValueError('invalid drift plan v1 schema')
    if type(plan['schema_version']) is not int or plan['schema_version'] != 1 or plan['profile'] != PLAN_PROFILE:
        raise ValueError('invalid drift plan v1 schema')
    if not _sha(plan['stream_sha256']):
        raise ValueError('stream identity must be a SHA-256')
    revisions = plan['reference_revisions_sha256']
    if not isinstance(revisions, list) or not revisions or not all(map(_sha, revisions)) \
            or len(set(revisions)) != len(revisions):
        raise ValueError('reference revisions must be unique SHA-256 hashes')
    if not _positive_int(plan['source_schema_version']):
        raise ValueError('source schema version must be a positive integer')
    if plan['unit'] != 'task_root':
        raise ValueError('the drift unit is the independent task root')
    strata = _names(plan['strata'], 'strata', allow_empty=False, allowed=STRATUM_FIELDS)
    features = _names(plan['features'], 'feature')
    required = _names(plan['required_fields'], 'required')
    if set(features) & RESERVED:
        raise ValueError('features must be observable task fields, not run metadata or strata')
    if set(strata) - set(required):
        raise ValueError('every stratum field must be a required field')
    capture = plan['capture']
    if not isinstance(capture, dict) or set(capture) != {'min_coverage', 'max_coverage_drop'} \
            or not _number(capture['min_coverage']) or capture['min_coverage'] == 0 \
            or not _number(capture['max_coverage_drop']):
        raise ValueError('invalid capture eligibility rule')
    if plan['method'] != 'fixed_window':
        raise ValueError('only non-overlapping fixed windows are supported')
    window = plan['reference_window']
    if not isinstance(window, list) or len(window) != 2 or not all(map(_finite, window)) or window[1] <= window[0]:
        raise ValueError('invalid reference window')
    if not _finite(plan['window_length']) or plan['window_length'] <= 0:
        raise ValueError('invalid window length')
    if not _positive_int(plan['max_windows']) or not _positive_int(plan['min_exposure']):
        raise ValueError('maximum windows and minimum exposure must be positive integers')
    effect = plan['effect_size']
    if not isinstance(effect, dict) or set(effect) != set(EFFECTS) \
            or not all(_number(effect[k]) and effect[k] > 0 for k in EFFECTS):
        raise ValueError('invalid effect sizes of interest')
    budget = plan['alert_budget']
    if not isinstance(budget, dict) or set(budget) != set(STATISTICAL_CHANNELS) \
            or not all(_number(budget[k]) and budget[k] > 0 for k in STATISTICAL_CHANNELS) \
            or sum(budget.values()) >= 1:
        raise ValueError('alert budget must allocate a positive false-alert rate to each statistical channel')
    if plan['multiplicity'] != 'bonferroni_windows_and_strata':
        raise ValueError('unsupported multiplicity family')
    if plan['reset_policy'] != 'new_plan_retains_history':
        raise ValueError('a reset must be a new plan that retains history and spent error')
    if plan['action'] not in ACTIONS:
        raise ValueError('drift action must be observe or review')
    return policy_sha256(plan)


def _digest(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _category(value):
    if isinstance(value, bool) or (isinstance(value, (str, int)) and len(str(value)) <= 128 and str(value)):
        return json.dumps(value)
    return UNKNOWN


def _known_identity(value):
    return isinstance(value, str) and bool(value.strip()) and value.strip().casefold() not in UNKNOWN_IDENTITIES


def _stratum(plan, run):
    values = [run.get(name) for name in plan['strata']]
    if not all(_known_identity(value) and len(value) <= 128 for value in values):
        return None
    return json.dumps(values)


def _issues(plan, run, revisions):
    """Return why a timed in-window run is ineligible; an empty list means complete capture."""
    issues = []
    if run.get('schema_version') != plan['source_schema_version'] or type(run.get('schema_version')) is not int:
        issues.append('schema_mismatch')
    if run.get('capture') != 'complete':
        issues.append('capture_' + (run.get('capture') if run.get('capture') in CAPTURE_STATES else UNKNOWN))
    if run.get('truncated') is not False:
        issues.append('truncated')
    if run.get('parse_rejected') is not False:
        issues.append('parse_rejected')
    if any(run.get(name) is None for name in plan['required_fields']):
        issues.append('missing_required_fields')
    if not _known_identity(run.get('task_root')):
        issues.append('unknown_task_root')
    revision = run.get('harness_revision')
    if not _known_identity(revision):
        issues.append('unknown_harness_identity')
    elif revisions is not None and root_sha256(revision) not in revisions:
        issues.append('revision_outside_reference')
    return issues


def _snapshot(plan, run):
    """Retain immutable fields used by the detector, not caller-owned nested values."""
    fields = {'started', 'cursor', 'schema_version', 'capture', 'truncated', 'parse_rejected',
              'task_root', 'harness_revision', 'outcome', 'outcome_provenance',
              *plan['required_fields'], *plan['strata'], *plan['features']}
    result = {}
    for name in fields:
        if name in run:
            value = run[name]
            result[name] = str(value) if isinstance(value, str) else value if type(value) in (int, float, bool, type(None)) else ()
    return result


def _summarize(plan, runs, start, end, revisions=None):
    """One bounded pass over ``runs``; only counts and per-root state are retained."""
    counts = dict(runs=0, eligible_runs=0, duplicates_suppressed=0, conflicting_duplicates=0, untimed_runs=0,
                  outside_window=0, cursor_unknown=0, cursor_gaps=0, cursor_collisions=0)
    issues, seen, cursors, roots = {}, {}, set(), {}
    for index, run in enumerate(runs):
        if index >= MAX_INPUT_RUNS: raise ValueError('drift input run bound exceeded')
        if not isinstance(run, dict) or not _known_identity(run.get('run_id')):
            raise ValueError('every run requires a run_id')
        signature = hashlib.sha256(json.dumps(run, sort_keys=True, default=repr).encode()).hexdigest()
        key = root_sha256(run['run_id'])
        versions = seen.setdefault(key, {})
        if signature in versions:
            counts['duplicates_suppressed'] += 1
        else:
            versions[signature] = _snapshot(plan, run)
    conflicts = [versions for versions in seen.values() if len(versions) > 1]
    counts['conflicting_duplicates'] = sum(len(versions) - 1 for versions in conflicts)
    conflict_roots, unresolved_root = set(), False
    for versions in conflicts:
        for run in versions.values():
            root = run.get('task_root')
            if _known_identity(root): conflict_roots.add(root_sha256(root))
            else: unresolved_root = True
    for versions in seen.values():
        if len(versions) != 1: continue
        run = next(iter(versions.values()))
        if not _finite(run.get('started')):
            counts['untimed_runs'] += 1
            continue
        if not start <= run['started'] < end:
            counts['outside_window'] += 1
            continue
        counts['runs'] += 1
        cursor = run.get('cursor')
        if type(cursor) is not int:
            counts['cursor_unknown'] += 1
        elif cursor in cursors:
            counts['cursor_collisions'] += 1
        else:
            cursors.add(cursor)
        found = _issues(plan, run, revisions)
        if unresolved_root or _known_identity(run.get('task_root')) and root_sha256(run['task_root']) in conflict_roots:
            found.append('conflicting_root_identity')
        for issue in found:
            issues[issue] = issues.get(issue, 0) + 1
        if found:
            continue
        counts['eligible_runs'] += 1
        state = roots.setdefault(root_sha256(run['task_root']), dict(
            stratum=_stratum(plan, run), features={name: _category(run.get(name)) for name in plan['features']},
            verified=set(), missing_label=False, unverified=False))
        if state['stratum'] != _stratum(plan, run):
            state['stratum'] = None
        for name in plan['features']:
            if state['features'][name] != _category(run.get(name)):
                state['features'][name] = UNKNOWN
        outcome = run.get('outcome')
        if type(outcome) is not bool:
            state['missing_label'] = True
        elif run.get('outcome_provenance') in VERIFIED_PROVENANCE:
            state['verified'].add(outcome)
        else:
            state['unverified'] = True
    if cursors:
        counts['cursor_gaps'] = max(cursors) - min(cursors) + 1 - len(cursors)
    counts['ineligible'] = dict(sorted(issues.items()))
    counts['coverage'] = counts['eligible_runs'] / counts['runs'] if counts['runs'] else None
    return counts, _channels(plan, roots)


def _bounded(counter, value):
    if value not in counter and len(counter) >= MAX_CATEGORIES:
        value = OTHER
    counter[value] = counter.get(value, 0) + 1
    return value


def _channels(plan, roots):
    strata, features, performance = {}, {name: {} for name in plan['features']}, {}
    unknown = dict(stratum_roots=0, missing_label_roots=0, unverified_roots=0, mixed_outcome_roots=0)
    for state in roots.values():
        for name, value in state['features'].items():
            _bounded(features[name], value)
        if state['stratum'] is None:
            unknown['stratum_roots'] += 1
            continue
        stratum = _bounded(strata, state['stratum'])
        if state['missing_label'] or state['unverified'] or len(state['verified']) != 1:
            unknown['missing_label_roots' if state['missing_label'] else 'unverified_roots' if state['unverified']
                    else 'mixed_outcome_roots'] += 1
            continue
        cell = performance.setdefault(stratum, dict(verified_roots=0, failures=0))
        cell['verified_roots'] += 1
        cell['failures'] += False in state['verified']
    return dict(roots=len(roots), strata=dict(sorted(strata.items())),
                features={k: dict(sorted(v.items())) for k, v in features.items()},
                performance=dict(sorted(performance.items())), unknown=unknown)


def _capture_reasons(plan, counts, reference_coverage=None):
    reasons = ['schema_mismatch'] if counts['ineligible'].get('schema_mismatch') else []
    reasons += [reason for reason, key in (('conflicting_duplicate_runs', 'conflicting_duplicates'),
                                           ('cursor_gap', 'cursor_gaps'), ('cursor_collision', 'cursor_collisions'),
                                           ('cursor_unknown', 'cursor_unknown'), ('untimed_runs', 'untimed_runs'))
                if counts[key]]
    coverage = counts['coverage']
    if coverage is not None and coverage < plan['capture']['min_coverage']:
        reasons.append('coverage_below_minimum')
    if coverage is not None and reference_coverage is not None \
            and reference_coverage - coverage > plan['capture']['max_coverage_drop']:
        reasons.append('coverage_drop_above_maximum')
    return reasons


def freeze_reference(plan, runs):
    """Summarize the plan's reference window as counts; refuse a reference with a capture alert."""
    plan_sha = check_plan(plan)
    start, end = plan['reference_window']
    counts, channels = _summarize(plan, runs, start, end, set(plan['reference_revisions_sha256']))
    reasons = _capture_reasons(plan, counts)
    if reasons:
        raise ValueError('reference window has capture alerts: ' + ', '.join(reasons))
    if channels['roots'] < plan['min_exposure']:
        raise ValueError('reference window has fewer eligible task roots than the minimum exposure')
    body = dict(schema_version=1, profile=REFERENCE_PROFILE, plan_sha256=plan_sha, window=[start, end],
                capture=counts, **channels, publication_allowed=False)
    body['sha256'] = _digest(body)
    return body


def _check_reference(reference, plan_sha):
    if not isinstance(reference, dict) or reference.get('profile') != REFERENCE_PROFILE \
            or reference.get('schema_version') != 1:
        raise ValueError('drift reference v1 required')
    if reference.get('plan_sha256') != plan_sha:
        raise ValueError('reference was frozen under a different plan')
    body = {k: v for k, v in reference.items() if k != 'sha256'}
    if reference.get('sha256') != _digest(body):
        raise ValueError('reference digest does not match its content')


def _statistical(plan, name, capture_state, current, extra):
    reasons = ['statistical_detector_not_implemented']
    if capture_state == 'alert':
        reasons.append('capture_alert_in_window')
    if current['roots'] < plan['min_exposure']:
        reasons.append('exposure_below_minimum')
    return dict(state='insufficient_evidence', reasons=reasons, tested=False,
                per_window_alpha=plan['alert_budget'][name] / plan['max_windows'],
                effect_size=plan['effect_size']['task_mix_total_variation' if name == 'task_mix'
                                                else 'failure_rate_increase'], **extra)


def _performance(plan, current, reference):
    strata = {}
    for key in sorted(set(current['performance']) | set(reference['performance'])):
        cells = {side: dict(source['performance'].get(key, dict(verified_roots=0, failures=0)))
                 for side, source in (('reference', reference), ('current', current))}
        for cell in cells.values():
            cell['failure_rate'] = cell['failures'] / cell['verified_roots'] if cell['verified_roots'] else None
        cells['comparable'] = all(cell['verified_roots'] >= plan['min_exposure'] for cell in cells.values())
        strata[key] = cells
    return dict(strata=strata, unknown=current['unknown'],
                comparable_strata=sum(cell['comparable'] for cell in strata.values()))


def assess(plan, reference, runs, window_index):
    """Return the ``drift-receipt-v1`` for monitored window ``window_index`` after the reference."""
    plan_sha = check_plan(plan)
    _check_reference(reference, plan_sha)
    if type(window_index) is not int or not 0 <= window_index < plan['max_windows']:
        raise ValueError('window outside the frozen schedule; a reset needs a new plan that retains history')
    start = plan['reference_window'][1] + window_index * plan['window_length']
    end = start + plan['window_length']
    counts, current = _summarize(plan, runs, start, end)
    reasons = _capture_reasons(plan, counts, reference['capture']['coverage'])
    if reasons:
        capture_state = 'alert'
    else:
        capture_state = 'insufficient_evidence' if counts['runs'] < plan['min_exposure'] else 'no_alert'
        reasons = ['no_runs_in_window'] if not counts['runs'] else ['exposure_below_minimum'] if \
            capture_state == 'insufficient_evidence' else []
    capture = dict(state=capture_state, reasons=reasons, reference_coverage=reference['capture']['coverage'],
                   **counts)
    mix = dict(reference=reference['strata'], current=current['strata'],
               new_strata=sorted(set(current['strata']) - set(reference['strata'])),
               absent_strata=sorted(set(reference['strata']) - set(current['strata'])),
               unknown_stratum_roots=current['unknown']['stratum_roots'],
               features=dict(reference=reference['features'], current=current['features']))
    channels = dict(capture=capture,
                    task_mix=_statistical(plan, 'task_mix', capture_state, current, mix),
                    performance=_statistical(plan, 'performance', capture_state, current,
                                             _performance(plan, current, reference)))
    alerted = any(channel['state'] == 'alert' for channel in channels.values())
    body = dict(schema_version=1, profile=RECEIPT_PROFILE, plan_sha256=plan_sha, reference_sha256=reference['sha256'],
                window=dict(index=window_index, start=start, end=end, max_windows=plan['max_windows']),
                unit='task_root', channels=channels, action=plan['action'] if alerted else 'observe',
                regression_label='not_established', causal_claim=False, automatic_rollback=False,
                promotion_authority='none', publication_allowed=False, limitations=list(LIMITATIONS))
    body['sha256'] = _digest(body)
    return body


def _canonical_stratum(key, fields):
    if key == OTHER: return True  # The aggregate is recognized but cannot yield a distance.
    if not isinstance(key, str) or len(key) > 1024: return False
    try: values = json.loads(key)
    except (ValueError, TypeError, RecursionError): return False
    return (isinstance(values, list) and len(values) == len(fields)
            and all(_known_identity(value) and len(value) <= 128 for value in values)
            and json.dumps(values) == key)


def describe_task_mix(plan, reference, receipt):
    """Compare declared stratum proportions descriptively; this is not a drift test."""
    plan_sha = check_plan(plan)
    _check_reference(reference, plan_sha)
    if (not isinstance(receipt, dict) or receipt.get('profile') != RECEIPT_PROFILE
            or type(receipt.get('schema_version')) is not int or receipt['schema_version'] != 1
            or receipt.get('plan_sha256') != plan_sha or receipt.get('reference_sha256') != reference['sha256']
            or receipt.get('sha256') != _digest({k: v for k, v in receipt.items() if k != 'sha256'})):
        raise ValueError('bound drift receipt required')
    try:
        capture, mix = receipt['channels']['capture'], receipt['channels']['task_mix']
        ref_counts, current_counts = reference['strata'], mix['current']
        ref_unknown, current_unknown = reference['unknown']['stratum_roots'], mix['unknown_stratum_roots']
        ref_coverage, current_coverage = reference['capture']['coverage'], capture['coverage']
        window = receipt['window']; index = window['index']
        if type(index) is not int or not 0 <= index < plan['max_windows']:
            raise ValueError('drift receipt does not match frozen inputs')
        start = plan['reference_window'][1] + index * plan['window_length']
        if (window != dict(index=index, start=start, end=start + plan['window_length'], max_windows=plan['max_windows'])
                or mix['reference'] != ref_counts or capture['reference_coverage'] != ref_coverage):
            raise ValueError('drift receipt does not match frozen inputs')
        if (not all(isinstance(counts, dict) and len(counts) <= MAX_CATEGORIES + 1
                    and all(_canonical_stratum(key, plan['strata']) and type(value) is int and 0 < value <= MAX_INPUT_RUNS
                            for key, value in counts.items()) for counts in (ref_counts, current_counts))
                or type(ref_unknown) is not int or ref_unknown < 0
                or type(current_unknown) is not int or current_unknown < 0):
            raise ValueError('invalid task-mix counts')
    except (KeyError, TypeError, AttributeError):
        raise ValueError('invalid task-mix inputs') from None
    ref_total, current_total = sum(ref_counts.values()), sum(current_counts.values())
    if (type(reference.get('roots')) is not int or ref_total + ref_unknown != reference['roots']
            or type(capture.get('eligible_runs')) is not int or current_total + current_unknown > capture['eligible_runs']
            or not 0 <= capture['eligible_runs'] <= MAX_INPUT_RUNS or reference['roots'] > MAX_INPUT_RUNS):
        raise ValueError('task-mix exposure does not match counts')
    reasons = []
    if (capture.get('state') != 'no_alert' or type(ref_coverage) not in (int, float) or ref_coverage != 1
            or type(current_coverage) not in (int, float) or current_coverage != 1):
        reasons.append('capture_not_complete')
    if ref_unknown or current_unknown: reasons.append('unknown_strata')
    if OTHER in ref_counts or OTHER in current_counts: reasons.append('collapsed_categories')
    if min(ref_total, current_total) < plan['min_exposure']: reasons.append('root_exposure_below_minimum')
    distance = None if reasons else 0.5 * math.fsum(abs(ref_counts.get(key, 0) / ref_total
        - current_counts.get(key, 0) / current_total) for key in set(ref_counts) | set(current_counts))
    return dict(schema_version=1, profile='drift-task-mix-description-v1', plan_sha256=plan_sha,
        reference_sha256=reference['sha256'], receipt_sha256=receipt['sha256'],
        status='insufficient_evidence' if reasons else 'descriptive_only', reasons=reasons,
        total_variation=distance, reference_roots=ref_total, current_roots=current_total,
        statistical_tested=False, alert=False, input_authority='unverified', root_independence='unverified',
        causal_claim=False, promotion_authority='none', publication_allowed=False)
