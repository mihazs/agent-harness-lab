"""Read-only side-by-side review model for one baseline/candidate experiment (#37).

Pairs records by task/root/family/repeat identity, never by array position, and
aligns timelines by stable event IDs or explicit mappings only. Events are
projected onto an allowlist of metadata fields, so message bodies, tool
arguments/results and reasoning never reach the report. Missing data is unknown,
not failure; a supplied backend analysis is copied verbatim, never recomputed.
"""
from collections import Counter
import json
import math

from .regressions import sha256

PROFILE = 'ahl-side-by-side-experiment-v1'
REPORT_PROFILE = 'ahl-side-by-side-report-v1'
ARMS = ('baseline', 'candidate')
VERDICTS = ('improved', 'regressed', 'unchanged', 'unknown', 'not_comparable')
EXECUTION_KINDS = ('synthetic', 'replay', 'real_model')
CAPTURES = ('complete', 'partial', 'unknown')
EVENT_KINDS = ('tool_call', 'tool_result', 'error', 'cancellation', 'coverage_gap', 'state_revision', 'skill_signal')
EVENT_FIELDS = ('event_id', 'kind', 'name', 'status', 'error_class')
USAGE_FIELDS = ('duration_ms', 'input_tokens', 'output_tokens')
IDENTITY = ('task', 'root', 'family', 'repeat')
MAX_TRIALS, MAX_EVENTS, MAX_MAPPINGS, MAX_TEXT = 5000, 200, 5000, 200


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= MAX_TEXT and not any(ord(c) < 32 for c in value)


def _key(item):
    """Pair identity, or None when any identity field is absent or malformed."""
    if not isinstance(item, dict): return None
    if not all(_text(item.get(name)) for name in IDENTITY[:3]): return None
    repeat = item.get('repeat')
    if type(repeat) is not int or repeat < 0: return None
    return tuple(item[name] for name in IDENTITY)


def _event(raw):
    if not isinstance(raw, dict) or not _text(raw.get('event_id')) or raw.get('kind') not in EVENT_KINDS:
        return None, 0
    if any(name in raw and not _text(raw[name]) for name in EVENT_FIELDS):
        return None, 0
    return {name: raw[name] for name in EVENT_FIELDS if name in raw}, len(set(raw) - set(EVENT_FIELDS))


def _usage(raw):
    if raw is None: return {name: None for name in USAGE_FIELDS}
    if not isinstance(raw, dict): raise ValueError('usage object required')
    result = {}
    for name in USAGE_FIELDS:
        value = raw.get(name)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise ValueError('invalid usage value')
        result[name] = value
    return result


def _trial(raw, experiment):
    """Projected arm record; `problems` lists why it cannot support a comparison. Rejected values become None."""
    problems, kept = [], {}
    kept['arm'] = raw.get('arm') if raw.get('arm') in ARMS else None
    if kept['arm'] is None: problems.append('unknown_arm')
    if not _text(raw.get('trial_id')): problems.append('invalid_trial_id')
    if raw.get('experiment_id') != experiment['experiment_id']: problems.append('experiment_mismatch')
    for name in ('harness_hash', 'grader_hash'):
        try: sha256(raw.get(name)); kept[name] = raw[name]
        except ValueError: problems.append('invalid_' + name); kept[name] = None
    kept['runtime_stratum'] = raw.get('runtime_stratum') if _text(raw.get('runtime_stratum')) else None
    if kept['runtime_stratum'] is None: problems.append('invalid_runtime_stratum')
    status, outcome = raw.get('status'), raw.get('outcome')
    if not _text(status): problems.append('invalid_status'); status = None
    if outcome is not None and type(outcome) is not bool: problems.append('invalid_outcome'); outcome = None
    try: usage = _usage(raw.get('usage'))
    except ValueError: problems.append('invalid_usage'); usage = {name: None for name in USAGE_FIELDS}
    events, invalid, omitted = [], 0, 0
    raw_events = raw.get('events', [])
    if not isinstance(raw_events, list): problems.append('invalid_events'); raw_events = []
    for position, item in enumerate(raw_events[:MAX_EVENTS]):
        event, dropped = _event(item)
        if event is None: invalid += 1; continue
        events.append(dict(event, position=position)); omitted += dropped
    capture = raw.get('capture') if raw.get('capture') in CAPTURES else 'unknown'
    return dict(trial_id=raw.get('trial_id') if _text(raw.get('trial_id')) else None, arm=kept['arm'],
                status=status, outcome=outcome, grader_hash=kept['grader_hash'], harness_hash=kept['harness_hash'],
                runtime_stratum=kept['runtime_stratum'], capture=capture, usage=usage, events=events,
                events_total=len(raw_events), events_truncated=len(raw_events) > MAX_EVENTS,
                invalid_events=invalid, omitted_event_fields=omitted, problems=problems)


def _verdict(key, arms, planned, experiment, duplicate_ids):
    reasons = []
    if key not in planned: reasons.append('unplanned_pair')
    for arm in ARMS:
        if len(arms[arm]) > 1: reasons.append('duplicate_' + arm)
    records = [record for records in arms.values() for record in records]
    if any(record['problems'] for record in records): reasons.append('invalid_record')
    if any(record['trial_id'] in duplicate_ids for record in records): reasons.append('duplicate_trial_id')
    if not reasons:  # each arm now holds at most one valid record; a lone arm is checked against the experiment too
        present = {arm: arms[arm][0] for arm in ARMS if arms[arm]}
        if any(record['harness_hash'] != experiment[arm + '_hash'] for arm, record in present.items()):
            reasons.append('harness_hash_mismatch')
        if any(record['grader_hash'] != experiment['grader_hash'] for record in present.values()):
            reasons.append('grader_hash_mismatch')
        if len(present) == 2 and present['baseline']['runtime_stratum'] != present['candidate']['runtime_stratum']:
            reasons.append('runtime_mismatch')
    if reasons: return 'not_comparable', reasons
    for arm in ARMS:
        if not arms[arm]: reasons.append('missing_' + arm)
        elif arms[arm][0]['status'] != 'graded' or arms[arm][0]['outcome'] is None: reasons.append('ungraded_' + arm)
    if reasons: return 'unknown', reasons
    before, after = arms['baseline'][0]['outcome'], arms['candidate'][0]['outcome']
    return ('unchanged' if before == after else 'improved' if after else 'regressed'), []


def _timeline(arms, mappings):
    """Rows aligned by identical event IDs or an explicit mapping; nothing is matched by position or similarity."""
    if any(len(arms[arm]) != 1 for arm in ARMS):
        return dict(alignment='not_aligned', reason='requires_one_record_per_arm', rows=[]), list(mappings)
    baseline, candidate = arms['baseline'][0]['events'], arms['candidate'][0]['events']
    ids = {arm: [event['event_id'] for event in events] for arm, events in zip(ARMS, (baseline, candidate))}
    rows_of = lambda events, match, side: [dict(match=match, **{side: event}) for event in events]
    if any(len(set(values)) != len(values) for values in ids.values()):
        return dict(alignment='not_aligned', reason='duplicate_event_ids',
                    rows=rows_of(baseline, 'baseline_only', 'baseline') + rows_of(candidate, 'candidate_only', 'candidate')), list(mappings)
    shared, explicit, rejected, links = set(ids['baseline']) & set(ids['candidate']), {}, [], {}
    for mapping in mappings:  # exact duplicates are one link; any event named by two links rejects all of them
        links.setdefault((mapping['baseline_event_id'], mapping['candidate_event_id']), []).append(mapping)
    sources, targets = Counter(source for source, _ in links), Counter(target for _, target in links)
    for (source, target), group in links.items():
        if (source not in ids['baseline'] or target not in ids['candidate'] or source in shared or target in shared
                or sources[source] > 1 or targets[target] > 1):
            rejected += group
        else: explicit[source] = target
    by_id, used, rows = {event['event_id']: event for event in candidate}, set(), []
    for event in baseline:
        target = event['event_id'] if event['event_id'] in shared else explicit.get(event['event_id'])
        if target is None: rows.append(dict(match='baseline_only', baseline=event)); continue
        used.add(target)
        rows.append(dict(match='stable_id' if target == event['event_id'] else 'explicit_mapping', baseline=event, candidate=by_id[target]))
    rows += rows_of([event for event in candidate if event['event_id'] not in used], 'candidate_only', 'candidate')
    return dict(alignment='by_stable_id_and_explicit_mapping', reason=None, rows=rows), rejected


def _usage_summary(pairs):
    """Usage of records in pairs that can be compared or are unknown; not_comparable records are only counted."""
    result = {}
    for arm in ARMS:
        records = [record for pair in pairs if pair['verdict'] != 'not_comparable' for record in pair['arms'][arm]]
        excluded = sum(len(pair['arms'][arm]) for pair in pairs if pair['verdict'] == 'not_comparable')
        result[arm] = {}
        for name in USAGE_FIELDS:
            known = [record['usage'][name] for record in records if record['usage'][name] is not None]
            unknown = len(records) - len(known)
            result[arm][name] = dict(known_total=sum(known), known_records=len(known), unknown_records=unknown,
                                     total=sum(known) if records and not unknown else None, excluded_records=excluded)
    return result


def build(experiment, *, public=False):
    """Validate one synthetic/replay/live experiment record and return the private review model."""
    if public: raise ValueError('side-by-side review reports are private; public export is not implemented')
    if not isinstance(experiment, dict) or experiment.get('profile') != PROFILE:
        raise ValueError('unsupported side-by-side experiment profile')
    if not _text(experiment.get('experiment_id')): raise ValueError('experiment identity required')
    if experiment.get('execution_kind') not in EXECUTION_KINDS: raise ValueError('declared execution kind required')
    for name in ('baseline_hash', 'candidate_hash', 'grader_hash'): sha256(experiment.get(name))
    planned_raw, trials_raw = experiment.get('planned'), experiment.get('trials')
    mappings_raw = experiment.get('event_mappings', [])
    if not isinstance(planned_raw, list) or not planned_raw or not isinstance(trials_raw, list) or not isinstance(mappings_raw, list):
        raise ValueError('nonempty planned pair list, trial list and mapping list required')
    if len(planned_raw) > MAX_TRIALS or len(trials_raw) > MAX_TRIALS or len(mappings_raw) > MAX_MAPPINGS:
        raise ValueError('experiment exceeds the documented report bounds')
    planned = [_key(item) for item in planned_raw]
    if None in planned or len(set(planned)) != len(planned): raise ValueError('unique valid planned pair identities required')
    analysis = experiment.get('analysis')
    if analysis is not None:
        if not isinstance(analysis, dict) or not _text(analysis.get('status')): raise ValueError('analysis object with status required')
        analysis = json.loads(json.dumps(analysis, allow_nan=False))
    grouped, invalid_records = {key: {arm: [] for arm in ARMS} for key in planned}, []
    for index, raw in enumerate(trials_raw):
        key = _key(raw)
        if key is None:
            invalid_records.append(dict(index=index, reason='invalid_pair_identity')); continue
        record = _trial(raw, experiment)
        arm = record['arm'] if record['arm'] in ARMS else 'invalid_arm'
        if arm == 'invalid_arm': invalid_records.append(dict(index=index, reason='unknown_arm'))
        grouped.setdefault(key, {name: [] for name in ARMS}).setdefault(arm, []).append(record)
    counts = Counter(record['trial_id'] for arms in grouped.values() for records in arms.values() for record in records if record['trial_id'])
    duplicate_ids = {value for value, count in counts.items() if count > 1}
    mappings, invalid_mappings = {}, []
    for index, raw in enumerate(mappings_raw):
        key = _key(raw)
        if key is None or key not in grouped or not all(_text(raw.get(name)) for name in ('baseline_event_id', 'candidate_event_id')):
            invalid_mappings.append(dict(index=index, reason='invalid_mapping')); continue
        mappings.setdefault(key, []).append(dict(index=index, baseline_event_id=raw['baseline_event_id'], candidate_event_id=raw['candidate_event_id']))
    pairs, plan = [], set(planned)
    for key in [*planned, *sorted(set(grouped) - plan)]:
        arms = grouped[key]
        verdict, reasons = _verdict(key, arms, plan, experiment, duplicate_ids)
        timeline, rejected = _timeline(arms, mappings.get(key, []))
        invalid_mappings += [dict(index=mapping['index'], reason='unresolvable_or_conflicting_mapping') for mapping in rejected]
        capture = {arm: [record['capture'] for record in arms[arm]] or ['unknown'] for arm in ARMS}
        pairs.append(dict(dict(zip(IDENTITY, key)), planned=key in plan, verdict=verdict, reasons=reasons, arms=arms,
                          capture=capture, coverage_gap=any(value != 'complete' for values in capture.values() for value in values),
                          timeline=timeline))
    verdicts = Counter({name: 0 for name in VERDICTS}); verdicts.update(pair['verdict'] for pair in pairs)
    return dict(schema_version=1, profile=REPORT_PROFILE, report_kind='side_by_side_experiment',
        experiment_id=experiment['experiment_id'], execution_kind=experiment['execution_kind'],
        capability_claim='none' if experiment['execution_kind'] != 'real_model' else 'unverified',
        hashes={name: experiment[name] for name in ('baseline_hash', 'candidate_hash', 'grader_hash')},
        summary=dict(planned_pairs=len(planned), unplanned_pairs=len(pairs) - len(planned), trials_received=len(trials_raw),
                     missing_pairs=sum(1 for pair in pairs if pair['planned'] and not any(pair['arms'].values())),
                     comparable_pairs=sum(verdicts[name] for name in VERDICTS[:3]), verdicts=dict(verdicts),
                     invalid_records=len(invalid_records), invalid_mappings=len(invalid_mappings),
                     coverage_gaps=sum(1 for pair in pairs if pair['coverage_gap']), usage=_usage_summary(pairs)),
        pairs=pairs, invalid_records=invalid_records, invalid_mappings=sorted(invalid_mappings, key=lambda item: item['index']),
        analysis=analysis, analysis_source='supplied_verbatim' if analysis is not None else 'not_supplied',
        pooled_treatment_effect=None, causal_attribution='none', publication='private', publication_allowed=False,
        automatic_promotion=False, trace_bodies='omitted_metadata_only',
        bounds=dict(max_trials=MAX_TRIALS, max_events_per_trial=MAX_EVENTS, max_mappings=MAX_MAPPINGS, max_text=MAX_TEXT),
        limitations=['Per-pair verdicts are descriptive; any treatment effect comes only from the supplied analysis.',
                     'Missing, ungraded or uncaptured observations are unknown, not failures or zero usage.',
                     'A tool error is not a task failure, and timeline alignment is not a causal attribution.',
                     'Synthetic and replay executions are not model capability evidence.'])
