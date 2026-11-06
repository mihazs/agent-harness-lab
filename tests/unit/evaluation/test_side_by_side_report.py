"""Side-by-side review model pairing, verdicts, timelines and privacy (#37); synthetic records only."""
import copy
import json

import pytest

from agent_harness_lab.evaluation import benchmark
from agent_harness_lab.evaluation import side_by_side as sbs

BASE, CAND, GRADER = 'a' * 64, 'b' * 64, 'c' * 64
CANARY = 'SECRET-CANARY-</script><img src=x onerror=alert(1)>'


def ident(task, repeat=0, root=None, family='synthetic'):
    return dict(task=task, root=root or 'root-' + task, family=family, repeat=repeat)


def trial(task, arm, outcome, *, repeat=0, status='graded', events=None, **extra):
    record = dict(ident(task, repeat), trial_id=f'{task}-{repeat}-{arm}', experiment_id='exp-1', arm=arm,
                  harness_hash=BASE if arm == 'baseline' else CAND, grader_hash=GRADER, runtime_stratum='replay-runtime-1',
                  status=status, outcome=outcome, capture='complete',
                  usage=dict(duration_ms=10, input_tokens=5, output_tokens=2), events=events or [])
    record.update(extra)
    return record


def experiment(trials, planned=None, **extra):
    tasks = planned if planned is not None else sorted({(t['task'], t['repeat']) for t in trials})
    value = dict(profile=sbs.PROFILE, experiment_id='exp-1', execution_kind='synthetic', baseline_hash=BASE,
                 candidate_hash=CAND, grader_hash=GRADER, planned=[ident(task, repeat) for task, repeat in tasks], trials=trials)
    value.update(extra)
    return value


def pair(report, task, repeat=0):
    [found] = [p for p in report['pairs'] if (p['task'], p['repeat']) == (task, repeat)]
    return found


def verdicts(report):
    return {(p['task'], p['repeat']): (p['verdict'], p['reasons']) for p in report['pairs']}


def ab(task, before, after, **extra):
    return [trial(task, 'baseline', before, **extra), trial(task, 'candidate', after, **extra)]


def test_pairs_by_identity_so_record_order_cannot_change_a_verdict():
    trials = ab('fixed', False, True) + ab('broken', True, False) + ab('same', True, True)
    forward, backward = sbs.build(experiment(trials)), sbs.build(experiment(list(reversed(trials))))
    assert verdicts(forward) == verdicts(backward) == {
        ('broken', 0): ('regressed', []), ('fixed', 0): ('improved', []), ('same', 0): ('unchanged', [])}
    assert forward['summary']['verdicts'] == dict(improved=1, regressed=1, unchanged=1, unknown=0, not_comparable=0)
    assert forward['summary']['comparable_pairs'] == 3


def test_repeats_are_separate_pairs():
    trials = ab('task', False, True) + [trial('task', 'baseline', True, repeat=1), trial('task', 'candidate', False, repeat=1)]
    report = sbs.build(experiment(trials))
    assert (pair(report, 'task', 0)['verdict'], pair(report, 'task', 1)['verdict']) == ('improved', 'regressed')


def test_missing_and_ungraded_arms_are_unknown_never_failures():
    trials = ([trial('lost', 'baseline', True)] + ab('infra', True, None, status='infrastructure_error')
              + [trial('stop', 'baseline', False), trial('stop', 'candidate', None, status='cancelled')])
    report = sbs.build(experiment(trials, planned=[('lost', 0), ('infra', 0), ('stop', 0), ('absent', 0)]))
    assert verdicts(report) == {('lost', 0): ('unknown', ['missing_candidate']),
                                ('infra', 0): ('unknown', ['ungraded_baseline', 'ungraded_candidate']),
                                ('stop', 0): ('unknown', ['ungraded_candidate']),
                                ('absent', 0): ('unknown', ['missing_baseline', 'missing_candidate'])}
    assert (report['summary']['missing_pairs'], report['summary']['comparable_pairs']) == (1, 0)
    assert pair(report, 'stop')['arms']['candidate'][0]['status'] == 'cancelled'


def test_a_recorded_tool_error_is_not_a_task_failure():
    errors = [dict(event_id='e1', kind='tool_call', name='run_tests'), dict(event_id='e2', kind='error', error_class='timeout')]
    report = sbs.build(experiment([trial('t', 'baseline', False), trial('t', 'candidate', True, events=errors)]))
    assert pair(report, 't')['verdict'] == 'improved'


@pytest.mark.parametrize('change, reason', [
    pytest.param(dict(runtime_stratum='other-runtime'), 'runtime_mismatch', id='runtime'),
    pytest.param(dict(grader_hash='d' * 64), 'grader_hash_mismatch', id='grader'),
    pytest.param(dict(harness_hash=BASE), 'harness_hash_mismatch', id='harness'),
])
def test_mismatched_pairs_are_shown_but_not_comparable(change, reason):
    report = sbs.build(experiment([trial('t', 'baseline', False), trial('t', 'candidate', True, **change)]))
    assert verdicts(report) == {('t', 0): ('not_comparable', [reason])}
    assert report['summary']['comparable_pairs'] == 0
    assert pair(report, 't')['arms']['candidate'][0]['outcome'] is True


def test_a_lone_arm_with_foreign_hashes_is_not_comparable_rather_than_missing():
    report = sbs.build(experiment([trial('t', 'baseline', True, harness_hash=CAND, grader_hash='d' * 64)]))
    assert verdicts(report) == {('t', 0): ('not_comparable', ['harness_hash_mismatch', 'grader_hash_mismatch'])}


def test_a_trial_from_another_experiment_is_not_comparable_and_kept_out_of_usage_totals():
    foreign = trial('t', 'candidate', True, experiment_id='exp-2', usage=dict(duration_ms=1, input_tokens=999, output_tokens=1))
    report = sbs.build(experiment([trial('t', 'baseline', False), foreign] + ab('u', False, True)))
    assert verdicts(report)[('t', 0)] == ('not_comparable', ['invalid_record'])
    assert pair(report, 't')['arms']['candidate'][0]['problems'] == ['experiment_mismatch']
    usage = report['summary']['usage']
    assert usage['candidate']['input_tokens'] == dict(known_total=5, known_records=1, unknown_records=0, total=5,
                                                      excluded_records=1)
    assert usage['baseline']['input_tokens'] == dict(known_total=5, known_records=1, unknown_records=0, total=5,
                                                     excluded_records=1)


def test_duplicate_unplanned_and_invalid_records_are_preserved():
    trials = (ab('dup', False, True) + [trial('dup', 'candidate', False, trial_id='dup-extra')]
              + [trial('twin', 'baseline', False, trial_id='shared'), trial('twin', 'candidate', True),
                 trial('other', 'baseline', False, trial_id='shared'), trial('other', 'candidate', True)]
              + ab('extra', False, True)
              + [trial('bad', 'baseline', False), trial('bad', 'candidate', 'yes'), trial('odd', 'baseline', True),
                 trial('odd', 'sideways', True), dict(trial('t', 'baseline', True), repeat=-1), 'not-an-object'])
    report = sbs.build(experiment(trials, planned=[('dup', 0), ('twin', 0), ('other', 0), ('bad', 0), ('odd', 0)]))
    assert verdicts(report) == {('dup', 0): ('not_comparable', ['duplicate_candidate']),
                                ('twin', 0): ('not_comparable', ['duplicate_trial_id']),
                                ('other', 0): ('not_comparable', ['duplicate_trial_id']),
                                ('bad', 0): ('not_comparable', ['invalid_record']),
                                ('odd', 0): ('not_comparable', ['invalid_record']),
                                ('extra', 0): ('not_comparable', ['unplanned_pair'])}
    assert len(pair(report, 'dup')['arms']['candidate']) == 2
    assert pair(report, 'bad')['arms']['candidate'][0]['problems'] == ['invalid_outcome']
    assert report['invalid_records'] == [dict(index=12, reason='unknown_arm'), dict(index=13, reason='invalid_pair_identity'),
                                         dict(index=14, reason='invalid_pair_identity')]
    assert (report['summary']['unplanned_pairs'], report['summary']['trials_received']) == (1, 15)
    assert report['summary']['usage']['candidate']['input_tokens'] == dict(
        known_total=0, known_records=0, unknown_records=0, total=None, excluded_records=6)


def timeline(report, task='t'):
    return [(row['match'], row.get('baseline', {}).get('event_id'), row.get('candidate', {}).get('event_id'))
            for row in pair(report, task)['timeline']['rows']]


def test_timelines_align_only_by_stable_id_or_explicit_mapping():
    base = [dict(event_id='call-1', kind='tool_call', name='read'), dict(event_id='b-only', kind='tool_call', name='edit'),
            dict(event_id='old', kind='tool_result', status='ok')]
    cand = [dict(event_id='new', kind='tool_result', status='ok'), dict(event_id='call-1', kind='tool_call', name='read'),
            dict(event_id='c-only', kind='tool_call', name='edit')]
    mapping = [dict(ident('t'), baseline_event_id='old', candidate_event_id='new')]
    report = sbs.build(experiment([trial('t', 'baseline', False, events=base), trial('t', 'candidate', True, events=cand)],
                                  event_mappings=mapping))
    # Same name and position ('edit' second in both) is not a match without a shared ID or mapping.
    assert timeline(report) == [('stable_id', 'call-1', 'call-1'), ('baseline_only', 'b-only', None),
                                ('explicit_mapping', 'old', 'new'), ('candidate_only', None, 'c-only')]
    assert report['invalid_mappings'] == []


def test_conflicting_or_unresolvable_mappings_are_rejected_and_listed():
    base = [dict(event_id='x', kind='tool_call'), dict(event_id='y', kind='tool_call')]
    cand = [dict(event_id='x', kind='tool_call'), dict(event_id='z', kind='tool_call')]
    mappings = [dict(ident('t'), baseline_event_id='x', candidate_event_id='z'),
                dict(ident('t'), baseline_event_id='y', candidate_event_id='missing'),
                dict(ident('nowhere'), baseline_event_id='y', candidate_event_id='z')]
    report = sbs.build(experiment([trial('t', 'baseline', True, events=base), trial('t', 'candidate', True, events=cand)],
                                  event_mappings=mappings))
    assert timeline(report) == [('stable_id', 'x', 'x'), ('baseline_only', 'y', None), ('candidate_only', None, 'z')]
    assert report['invalid_mappings'] == [dict(index=0, reason='unresolvable_or_conflicting_mapping'),
                                          dict(index=1, reason='unresolvable_or_conflicting_mapping'),
                                          dict(index=2, reason='invalid_mapping')]


def mapped(base_ids, cand_ids, pairs):
    events = lambda ids: [dict(event_id=value, kind='tool_call') for value in ids]
    mappings = [dict(ident('t'), baseline_event_id=source, candidate_event_id=target) for source, target in pairs]
    report = sbs.build(experiment([trial('t', 'baseline', True, events=events(base_ids)),
                                   trial('t', 'candidate', True, events=events(cand_ids))], event_mappings=mappings))
    return timeline(report), [item['index'] for item in report['invalid_mappings']]


@pytest.mark.parametrize('base_ids, cand_ids, pairs, rows', [
    pytest.param(['a'], ['x', 'y'], [('a', 'x'), ('a', 'y')],
                 [('baseline_only', 'a', None), ('candidate_only', None, 'x'), ('candidate_only', None, 'y')], id='one-source'),
    pytest.param(['a', 'b'], ['c'], [('a', 'c'), ('b', 'c')],
                 [('baseline_only', 'a', None), ('baseline_only', 'b', None), ('candidate_only', None, 'c')], id='one-target'),
])
def test_every_mapping_in_a_conflict_is_rejected_whatever_its_order(base_ids, cand_ids, pairs, rows):
    assert mapped(base_ids, cand_ids, pairs) == mapped(base_ids, cand_ids, pairs[::-1]) == (rows, [0, 1])


def test_a_mapping_onto_a_shared_candidate_id_is_rejected_and_exact_duplicates_are_one_mapping():
    assert mapped(['x', 'y'], ['x'], [('y', 'x')]) == ([('stable_id', 'x', 'x'), ('baseline_only', 'y', None)], [0])
    assert mapped(['a'], ['b'], [('a', 'b'), ('a', 'b')]) == ([('explicit_mapping', 'a', 'b')], [])


def test_duplicate_event_ids_refuse_alignment():
    events = [dict(event_id='e', kind='tool_call'), dict(event_id='e', kind='tool_result')]
    report = sbs.build(experiment([trial('t', 'baseline', True, events=events), trial('t', 'candidate', True, events=events[:1])]))
    view = pair(report, 't')['timeline']
    assert (view['alignment'], view['reason']) == ('not_aligned', 'duplicate_event_ids')
    assert [row['match'] for row in view['rows']] == ['baseline_only', 'baseline_only', 'candidate_only']


def test_trace_bodies_never_reach_the_report():
    events = [dict(event_id='e1', kind='tool_call', name='bash', arguments=CANARY, result=CANARY, reasoning=CANARY),
              dict(event_id='e2', kind='message', content=CANARY), dict(event_id='e3', kind='tool_result', status=None)]
    raw = [trial('t', 'baseline', False, events=events, transcript=CANARY, message_body=CANARY),
           trial('t', 'candidate', True, events=events)]
    report = sbs.build(experiment(raw))
    assert 'CANARY' not in json.dumps(report)
    record = pair(report, 't')['arms']['baseline'][0]
    assert record['events'] == [dict(event_id='e1', kind='tool_call', name='bash', position=0)]
    assert (record['omitted_event_fields'], record['invalid_events'], report['trace_bodies']) == (3, 2, 'omitted_metadata_only')


@pytest.mark.parametrize('field', ['arm', 'harness_hash', 'grader_hash', 'runtime_stratum'])
@pytest.mark.parametrize('value', [CANARY + '\x00' + 'x' * 300, dict(transcript=CANARY)], ids=['long-text', 'object'])
def test_rejected_trial_fields_are_not_copied_into_the_report(field, value):
    report = sbs.build(experiment([dict(trial('t', 'baseline', False), **{field: value}), trial('t', 'candidate', True)]))
    assert 'CANARY' not in json.dumps(report)
    assert pair(report, 't')['verdict'] == 'not_comparable'


def test_unknown_capture_and_usage_stay_unknown():
    trials = [trial('t', 'baseline', False), trial('t', 'candidate', True, usage=dict(duration_ms=7))]
    del trials[0]['capture']
    trials[1]['capture'] = 'partial'
    report = sbs.build(experiment(trials + ab('u', True, True)))
    assert pair(report, 't')['capture'] == dict(baseline=['unknown'], candidate=['partial'])
    assert (pair(report, 't')['coverage_gap'], pair(report, 'u')['coverage_gap']) == (True, False)
    assert report['summary']['coverage_gaps'] == 1
    tokens = report['summary']['usage']['candidate']['input_tokens']
    assert tokens == dict(known_total=5, known_records=1, unknown_records=1, total=None, excluded_records=0)
    assert report['summary']['usage']['baseline']['duration_ms']['total'] == 20


def test_supplied_analysis_is_copied_verbatim_and_never_recomputed():
    analysis = dict(status='inconclusive', reason='uncertainty_or_margin_not_met', primary=dict(mean=0.5, low=-0.4, high=1.0))
    source = experiment(ab('t', False, True), analysis=analysis)
    before = copy.deepcopy(source)
    report = sbs.build(source)
    assert (report['analysis'], report['analysis_source']) == (analysis, 'supplied_verbatim')
    assert (report['pooled_treatment_effect'], report['causal_attribution']) == (None, 'none')
    analysis['status'] = 'supported'
    assert report['analysis']['status'] == 'inconclusive'
    analysis['status'] = 'inconclusive'
    assert source == before
    assert sbs.build(experiment(ab('t', False, True)))['analysis_source'] == 'not_supplied'


def test_event_lists_are_truncated_visibly_and_trial_counts_are_bounded(monkeypatch):
    events = [dict(event_id=f'e{i}', kind='tool_call') for i in range(sbs.MAX_EVENTS + 3)]
    record = pair(sbs.build(experiment(ab('t', True, True, events=events))), 't')['arms']['baseline'][0]
    assert (len(record['events']), record['events_total'], record['events_truncated']) == (sbs.MAX_EVENTS, sbs.MAX_EVENTS + 3, True)
    monkeypatch.setattr(sbs, 'MAX_TRIALS', 3)
    with pytest.raises(ValueError, match='bounds'):
        sbs.build(experiment(ab('t', True, True) + ab('u', True, True)))


def test_reports_are_private_and_rejected_by_public_export():
    report = sbs.build(experiment(ab('t', False, True)))
    assert (report['publication_allowed'], report['automatic_promotion'], report['capability_claim']) == (False, False, 'none')
    assert benchmark.public_export_allowed([report]) is False
    with pytest.raises(ValueError, match='private'):
        sbs.build(experiment(ab('t', False, True)), public=True)


@pytest.mark.parametrize('change, message', [
    pytest.param(dict(profile='other'), 'profile', id='profile'),
    pytest.param(dict(execution_kind='guess'), 'execution kind', id='execution-kind'),
    pytest.param(dict(grader_hash='C' * 64), 'SHA-256', id='hash'),
    pytest.param(dict(planned=[]), 'nonempty', id='empty-plan'),
    pytest.param(dict(planned=[ident('t'), ident('t')]), 'unique', id='duplicate-plan'),
    pytest.param(dict(analysis=dict(primary=1)), 'analysis', id='analysis-without-status'),
])
def test_malformed_experiments_are_refused(change, message):
    value = experiment(ab('t', False, True))
    value.update(change)
    with pytest.raises(ValueError, match=message):
        sbs.build(value)
