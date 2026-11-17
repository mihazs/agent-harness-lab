"""ATIF-v1.8 metadata importer (#38): mapping, disclosure, fail-closed rejection and unknown outcome/lineage. Synthetic documents only."""
import json

import pytest

from agent_harness_lab.interop import atif
from agent_harness_lab.interop.mapping import ATIF_FIELDS, CONTAINER, REASONS, Rejected
from agent_harness_lab.telemetry.ledger import Ledger
from agent_harness_lab.telemetry.schema import event, serialize, verified_outcome
from tests.support.atif_fixture import CANARY, agent_step, call, edit, full, raw, trajectory


def convert(document, **limits):
    return atif.convert(raw(document), atif.Limits(**limits))


def rejected(document, **limits):
    with pytest.raises(Rejected) as stopped:
        convert(document, **limits)
    assert stopped.value.reason in REASONS
    return stopped.value


def activities(events):
    return [row['activity'] for row in events]


def with_(document, mutate):
    return edit(document, mutate)


# --- version detection -------------------------------------------------------------------------------------------

@pytest.mark.parametrize('version', ['ATIF-v1.7', 'ATIF-v1.6', 'ATIF-v1.0', 'ATIF-v1.9', 'ATIF-v2.0', 'atif-v1.8', 'ATIF-v1.8.1', 8, None, ['ATIF-v1.8']],
    ids=['older-minor', 'older-recent', 'oldest', 'newer-minor', 'newer-major', 'case-differs', 'patch-suffix', 'number', 'null', 'list'])
def test_any_schema_version_but_the_pinned_one_is_rejected_with_the_pinned_version_named(version):
    stopped = rejected(with_(trajectory(), lambda d: d.update(schema_version=version)))
    assert stopped.reason == 'unsupported_schema_version'
    assert 'supported ATIF-v1.8' in str(stopped)


def test_missing_schema_version_is_rejected_as_unsupported_not_guessed():
    document = trajectory()
    del document['schema_version']
    assert rejected(document).reason == 'unsupported_schema_version'


def test_a_hostile_version_string_is_never_echoed_in_the_refusal():
    stopped = rejected(with_(trajectory(), lambda d: d.update(schema_version=CANARY)))
    assert CANARY not in str(stopped) and 'unrecognized' in str(stopped)


@pytest.mark.parametrize('body', [[], 'ATIF-v1.8', 7, None], ids=['array', 'string', 'number', 'null'])
def test_a_document_that_is_not_an_object_is_rejected_as_unsupported(body):
    with pytest.raises(Rejected) as stopped:
        atif.convert(json.dumps(body).encode())
    assert stopped.value.reason == 'unsupported_schema_version'


# --- deterministic mapping ----------------------------------------------------------------------------------------

def test_conversion_is_deterministic_for_identical_bytes():
    first, again = convert(full()), convert(full())
    assert first[0] == again[0] and first[1].to_dict() == again[1].to_dict()


def test_a_changed_document_gets_new_case_identities_so_reingestion_never_conflicts():
    original = {row['case_id'] for row in convert(trajectory())[0]}
    changed = {row['case_id'] for row in convert(with_(trajectory(), lambda d: d['steps'][0].update(message='different')))[0]}
    assert original.isdisjoint(changed)


def test_steps_become_ordered_metadata_events_in_source_order():
    events, report = convert(trajectory())
    assert activities(events) == ['user_message', 'agent_message', 'tool_call', 'tool_result']
    assert [row['capture_order'] for row in events] == [0, 1, 2, 3]
    assert [row['event_id'] for row in events] == ['1:message:0', '2:message:0', '2:call:0', '2:result:0']
    assert report.counts['steps'] == 2 and report.counts['events'] == 4


def test_a_call_and_its_result_are_linked_by_a_scoped_digest_not_the_source_id():
    events, _ = convert(trajectory())
    issued, answered = events[2], events[3]
    assert issued['call_id'] == answered['call_id'] and issued['call_id'] != 'c1'
    assert answered['capture_status'] == 'linked' and answered['execution_status'] == 'unknown'
    assert issued['tool_name'].startswith('tool:') and 'read_file' not in issued['tool_name']


def test_every_event_declares_the_source_and_never_a_complete_capture():
    events, _ = convert(full())
    assert {(row['source_format'], row['source_version'], row['capture_complete']) for row in events} == {('atif', 'ATIF-v1.8', False)}


def test_the_agent_and_model_survive_only_as_digests():
    events, _ = convert(trajectory())
    assert all(len(row['runtime_fingerprint']) == 64 and len(row['requested_model_fingerprint']) == 64 for row in events)
    assert 'agent-x' not in json.dumps(events) and 'model-x' not in json.dumps(events)


# --- disclosure ---------------------------------------------------------------------------------------------------

def test_every_declared_field_is_disclosed_as_retained_or_dropped_when_present():
    _, report = convert(full())
    seen = set(report.retained_fields) | set(report.dropped_fields)
    declared = {prefix + '.' + key for prefix, table in ATIF_FIELDS.items() for key, how in table.items() if how != CONTAINER}
    assert declared <= seen
    assert not set(report.retained_fields) & set(report.dropped_fields)


@pytest.mark.parametrize('path', ['steps[].message', 'steps[].extra', 'steps[].observation.results[].content', 'steps[].tool_calls[].extra',
    'steps[].metrics.cost_usd', 'agent.tool_definitions', 'final_metrics.total_cost_usd', 'trajectory.session_id'])
def test_content_cost_and_extension_fields_are_dropped_and_counted_never_retained(path):
    _, report = convert(full())
    assert report.dropped_fields[path] >= 1 and path not in report.retained_fields


def test_the_report_states_the_conversion_is_lossy_and_nothing_was_published_or_fetched():
    report = convert(full())[1].to_dict()
    assert report['lossy'] is True and report['round_trip'] == 'retained_metadata_only'
    assert (report['publication_allowed'], report['network_used'], report['referenced_files_read']) == (False, False, False)
    assert len(report['source_digest']) == 64


def test_an_undeclared_field_is_counted_by_location_and_its_name_is_never_recorded():
    document = with_(trajectory(), lambda d: d['steps'][0].update({CANARY: 1}))
    events, report = convert(document)
    assert report.dropped_fields['steps[].<undeclared>'] == 1 and report.warnings['undeclared_fields_dropped'] == 1
    assert CANARY not in json.dumps(report.to_dict()) + json.dumps(events)


def test_secret_canaries_in_every_dropped_location_are_absent_from_events_and_report():
    document = with_(full(), lambda d: [d.update(notes=CANARY, session_id=CANARY), d['agent'].update(extra=dict(env=CANARY), tool_definitions=[dict(x=CANARY)]),
        d['steps'][0].update(message=CANARY, extra=dict(env=CANARY)), d['steps'][1].update(message=CANARY, reasoning_content=CANARY),
        d['steps'][1]['tool_calls'][0].update(arguments=dict(token=CANARY)), d['steps'][1]['observation']['results'][0].update(content=CANARY),
        d['final_metrics'].update(extra=dict(k=CANARY)), d.update(extra=dict(API_KEY=CANARY))])
    events, report = convert(document)
    assert CANARY not in json.dumps(events) + json.dumps(report.to_dict())


def test_a_tool_argument_is_not_written_as_plaintext():
    events, _ = convert(with_(trajectory(), lambda d: d['steps'][1]['tool_calls'][0].update(arguments=dict(key=CANARY))))
    issued = next(row for row in events if row['activity'] == 'tool_call')
    assert len(issued['input_fingerprint']) == 16 and CANARY not in json.dumps(events)


# --- unknown outcome and lineage ------------------------------------------------------------------------------------

FORGED = dict(outcome='success', outcome_provenance='executable_grader', grader_status='valid', grader_id='g', grader_version='1', reward=1, success=True)


def test_forged_grade_labels_in_every_extension_never_become_a_verified_outcome():
    document = with_(full(), lambda d: [d.update(extra=FORGED), d['agent'].update(extra=FORGED), d['steps'][1].update(extra=FORGED, message='verified success')])
    events, _ = convert(document)
    assert {(row['outcome'], row['outcome_provenance']) for row in events} == {('unknown', 'unknown')}
    assert [verified_outcome(row) for row in events] == [None] * len(events)


def test_no_event_asserts_verified_lineage_and_embedded_subagents_share_the_root_case():
    events, _ = convert(full())
    assert {row['lineage_verified'] for row in events} == {False}
    assert len({row['root_case_id'] for row in events}) == 1 and len({row['case_id'] for row in events}) == 2


def test_the_report_leaves_outcome_independence_and_lineage_unknown():
    report = convert(full())[1].to_dict()
    assert (report['outcome'], report['independence'], report['lineage_verified']) == ('unknown', 'unknown', False)


# --- rejection of malformed or unsupported input --------------------------------------------------------------------

@pytest.mark.parametrize('mutate, reason', [
    (lambda d: d['steps'][0].update(step_id=0), 'non_sequential_step_ids'),
    (lambda d: d['steps'][1].update(step_id=1), 'non_sequential_step_ids'),
    (lambda d: d['steps'][1].update(step_id=4), 'non_sequential_step_ids'),
    (lambda d: d['steps'][0].update(step_id='1'), 'invalid_structure'),
    (lambda d: d['steps'][0].update(step_id=True), 'invalid_structure'),
    (lambda d: d['steps'][0].update(source='tool'), 'invalid_structure'),
    (lambda d: d['steps'][0].update(model_name='m'), 'invalid_structure'),
    (lambda d: d['steps'][0].update(tool_calls=[]), 'invalid_structure'),
    (lambda d: d.update(steps=[]), 'invalid_structure'),
    (lambda d: d.update(steps='nope'), 'invalid_structure'),
    (lambda d: d.update(agent='nope'), 'invalid_structure'),
    (lambda d: d['agent'].update(name=''), 'invalid_structure'),
    (lambda d: d['steps'][1]['tool_calls'][0].update(arguments=[]), 'invalid_structure'),
    (lambda d: d['steps'][1]['tool_calls'][0].update(tool_call_id=7), 'invalid_structure'),
    (lambda d: d['steps'][1]['observation']['results'][0].update(source_call_id=7), 'invalid_structure'),
    (lambda d: d['steps'][0].update(extra='text'), 'invalid_structure'),
    (lambda d: d['steps'][0].update(message=None), 'invalid_structure'),
    (lambda d: d['steps'][0].pop('message'), 'missing_field'),
    (lambda d: d.pop('steps'), 'missing_field'),
    (lambda d: d['agent'].pop('version'), 'missing_field'),
    (lambda d: d['steps'][1]['tool_calls'][0].pop('arguments'), 'missing_field'),
], ids=['step-zero', 'step-repeats', 'step-gap', 'step-text', 'step-bool', 'unknown-source', 'model-on-user-step', 'calls-on-user-step', 'no-steps',
        'steps-not-list', 'agent-not-object', 'empty-agent-name', 'arguments-not-object', 'call-id-number', 'source-call-id-number', 'extra-not-object',
        'message-null', 'message-missing', 'steps-missing', 'agent-version-missing', 'arguments-missing'])
def test_malformed_structure_is_rejected_with_a_typed_reason(mutate, reason):
    assert rejected(with_(trajectory(), mutate)).reason == reason


@pytest.mark.parametrize('stamp', ['yesterday', '2025-10-16T14:30:00', '', 20251016, None, '2025-13-40T00:00:00Z'],
    ids=['words', 'no-timezone', 'empty', 'number', 'null', 'impossible-date'])
def test_an_invalid_or_zoneless_timestamp_is_rejected_not_read_as_a_time(stamp):
    assert rejected(with_(trajectory(), lambda d: d['steps'][0].update(timestamp=stamp))).reason == 'invalid_timestamp'


@pytest.mark.parametrize('metrics, reason', [(dict(prompt_tokens=-1), 'invalid_measurement'), (dict(completion_tokens=1.5), 'invalid_measurement'),
    (dict(prompt_tokens=True), 'invalid_measurement'), (dict(prompt_tokens='9'), 'invalid_measurement'), (dict(prompt_tokens=2**63), 'invalid_measurement'),
    (dict(cost_usd=-0.1), 'invalid_measurement'), (dict(cost_usd=1e999), 'nonfinite_number'),
    (dict(prompt_tokens=10, cached_tokens=11), 'invalid_measurement'), (dict(logprobs='x'), 'invalid_structure')],
    ids=['negative', 'fractional-count', 'boolean', 'text', 'oversized', 'negative-cost', 'infinite-cost', 'cache-exceeds-prompt', 'logprobs-not-list'])
def test_an_invalid_or_nonfinite_measurement_is_rejected_never_clamped(metrics, reason):
    assert rejected(with_(trajectory(), lambda d: d['steps'][1].update(metrics=metrics))).reason == reason


def test_a_finite_json_literal_that_overflows_a_cost_is_an_invalid_measurement():
    document = with_(trajectory(), lambda d: d['steps'][1].update(metrics=dict(cost_usd=1)))
    encoded = raw(document).replace(b'"cost_usd": 1', b'"cost_usd": 1e999')
    with pytest.raises(Rejected) as stopped:
        atif.convert(encoded)
    assert stopped.value.reason == 'invalid_measurement'


@pytest.mark.parametrize('body, reason', [
    (b'{"schema_version": "ATIF-v1.8", "schema_version": "ATIF-v1.8"}', 'duplicate_json_key'),
    (b'{"schema_version": NaN}', 'nonfinite_number'),
    (b'{"schema_version": Infinity}', 'nonfinite_number'),
    (b'{"schema_version": ', 'invalid_json'),
    (b'\xff\xfe\x00', 'invalid_json'),
    (b'', 'invalid_json'),
    (b'[' * 100000, 'nesting_limit_exceeded'),
], ids=['duplicate-key', 'nan', 'infinity', 'truncated', 'not-utf8', 'empty', 'unbounded-nesting'])
def test_hostile_json_is_rejected_before_any_field_is_read(body, reason):
    with pytest.raises(Rejected) as stopped:
        atif.convert(body)
    assert stopped.value.reason == reason


def test_nesting_beyond_the_bound_inside_a_dropped_field_is_rejected():
    deep = current = {}
    for _ in range(40):
        current['k'] = current = {}
    assert rejected(with_(trajectory(), lambda d: d['steps'][1]['tool_calls'][0].update(arguments=deep))).reason == 'nesting_limit_exceeded'


@pytest.mark.parametrize('limit, reason', [(dict(max_steps=1), 'step_limit_exceeded'), (dict(max_events=3), 'event_limit_exceeded'),
    (dict(max_depth=2), 'nesting_limit_exceeded')], ids=['steps', 'events', 'depth'])
def test_each_configured_bound_rejects_instead_of_truncating(limit, reason):
    assert rejected(trajectory(), **limit).reason == reason


def test_more_embedded_subagents_than_the_bound_is_rejected():
    document = with_(trajectory(), lambda d: d.update(subagent_trajectories=[trajectory(trajectory_id=f'sub-{i}') for i in range(3)]))
    assert rejected(document, max_subagents=2).reason == 'subagent_limit_exceeded'


# --- media and content parts ---------------------------------------------------------------------------------------

HOSTILE_PATHS = ['/etc/passwd', 'file:///etc/shadow', 'https://example.invalid/leak.png', '../../outside.png', 'images/' + CANARY + '.png']


@pytest.mark.parametrize('path', HOSTILE_PATHS, ids=['absolute', 'file-url', 'http-url', 'traversal', 'relative-canary'])
def test_a_media_part_is_reported_as_unsupported_and_its_location_never_leaves_the_input(path):
    part = lambda kind, media_type: dict(type=kind, source=dict(media_type=media_type, path=path))
    document = with_(trajectory(), lambda d: d['steps'][0].update(message=[dict(type='text', text='look'), part('image', 'image/png'), part('audio', 'audio/wav')]))
    events, report = convert(document)
    assert report.unsupported_features['image_content_parts_not_loaded'] == 1 and report.unsupported_features['audio_content_parts_not_loaded'] == 1
    assert sum(row.get('missing_reason') == 'unsupported_media_content' for row in events) == 2
    assert path not in json.dumps(events) + json.dumps(report.to_dict())


@pytest.mark.parametrize('part', [dict(type='video', source=dict(path='x', media_type='video/mp4')), dict(type='image'), dict(type='text'),
    dict(type='text', text='x', source={}), dict(type='image', source=dict(path=3, media_type='image/png')), 'plain', None],
    ids=['video', 'image-without-source', 'text-without-text', 'text-with-source', 'path-not-text', 'string-part', 'null-part'])
def test_an_unknown_or_malformed_content_part_is_rejected(part):
    assert rejected(with_(trajectory(), lambda d: d['steps'][0].update(message=[part]))).reason == 'unsupported_content_part'


# --- calls, results, ordering and incomplete evidence ----------------------------------------------------------------

def statuses(events):
    return sorted((row['activity'], row['capture_status'], row.get('missing_reason')) for row in events if row['activity'] != 'agent_message'
                  and row['activity'] != 'user_message')


def test_a_call_that_never_returns_is_explicit_incomplete_evidence_not_a_failure_or_success():
    document = trajectory([agent_step(1, [call('c1')], results=[])])
    events, report = convert(document)
    assert ('capture_coverage', 'unmatched_call', 'capture_ended_without_result') in statuses(events)
    assert all(row['outcome'] == 'unknown' and row.get('execution_status') != 'returned' for row in events)
    assert report.counts['status:unmatched_call'] == 1


def test_a_result_for_an_unissued_call_is_marked_unmatched():
    events, _ = convert(trajectory([agent_step(1, [], results=[dict(source_call_id='ghost', content='x')])]))
    assert [row['capture_status'] for row in events if row['activity'] == 'tool_result'] == ['unmatched_result']


def test_a_result_ahead_of_its_call_is_never_silently_reordered_into_a_link():
    steps = [agent_step(1, [], results=[dict(source_call_id='late', content='x')]), agent_step(2, [call('late')], results=[])]
    events, _ = convert(trajectory(steps))
    marks = {row['capture_status'] for row in events if row['activity'] in ('tool_result', 'capture_coverage')}
    assert marks == {'unmatched_result', 'unmatched_call'}


def test_interleaved_calls_and_results_across_steps_link_by_identifier_not_position():
    steps = [agent_step(1, [call('a'), call('b')], results=[dict(source_call_id='b', content='x')]), agent_step(2, [], results=[dict(source_call_id='a', content='y')])]
    events, _ = convert(trajectory(steps))
    results = [row for row in events if row['activity'] == 'tool_result']
    assert [row['capture_status'] for row in results] == ['linked', 'linked']
    issued = {row['call_id']: row['event_id'] for row in events if row['activity'] == 'tool_call'}
    assert issued[results[0]['call_id']] == '1:call:1' and issued[results[1]['call_id']] == '1:call:0'


def test_a_reused_call_identifier_is_marked_duplicate_not_merged():
    events, _ = convert(trajectory([agent_step(1, [call('same')]), agent_step(2, [call('same')])]))
    assert [row['capture_status'] for row in events if row['activity'] == 'tool_call'] == ['unverified', 'duplicate_call']


def test_a_result_without_a_call_reference_stays_unlinked_and_is_counted_missing():
    events, report = convert(trajectory([agent_step(1, [], results=[dict(content='x')])]))
    result = next(row for row in events if row['activity'] == 'tool_result')
    assert 'call_id' not in result and result['missing_reason'] == 'source_call_id_absent'
    assert report.missing_fields['steps[].observation.results[].source_call_id'] == 1


def test_a_missing_timestamp_stays_missing_and_no_duration_is_invented():
    events, report = convert(trajectory())
    assert all('timestamp' not in row and 'elapsed_seconds' not in row for row in events)
    assert report.missing_fields['steps[].timestamp'] == 2


def test_present_timestamps_are_kept_exactly_and_never_turned_into_durations():
    document = with_(trajectory(), lambda d: [s.update(timestamp='2025-10-16T14:30:00Z') for s in d['steps']])
    events, _ = convert(document)
    assert {row['timestamp'] for row in events} == {'2025-10-16T14:30:00Z'} and all('elapsed_seconds' not in row for row in events)


def test_a_reasoning_step_records_that_reasoning_existed_and_nothing_else():
    events, _ = convert(trajectory([agent_step(1, reasoning_content=CANARY)]))
    reasoning = next(row for row in events if row['activity'] == 'reasoning')
    assert CANARY not in json.dumps(reasoning) and reasoning['actor'] == 'agent'


def test_a_step_copied_from_an_earlier_trajectory_is_not_imported_as_a_new_interaction():
    events, report = convert(trajectory([agent_step(1, [call('c1')]), agent_step(2, [call('c1')], is_copied_context=True)]))
    assert sum(row['activity'] == 'tool_call' for row in events) == 1
    assert report.unsupported_features['copied_context_steps_not_imported'] == 1 and report.counts['steps'] == 2


# --- usage ------------------------------------------------------------------------------------------------------

def usage(events):
    return [row for row in events if row['activity'] == 'response_usage']


def test_uncached_input_is_prompt_minus_cached_and_output_is_completion():
    events, _ = convert(trajectory([agent_step(1, metrics=dict(prompt_tokens=100, cached_tokens=30, completion_tokens=12))]))
    assert [(row['input_tokens'], row['output_tokens']) for row in usage(events)] == [(70, 12)]


def test_a_prompt_total_without_its_cached_share_yields_no_input_count():
    events, report = convert(trajectory([agent_step(1, metrics=dict(prompt_tokens=100, completion_tokens=12))]))
    (row,) = usage(events)
    assert 'input_tokens' not in row and row['output_tokens'] == 12 and row['missing_reason'] == 'cached_token_split_unavailable'
    assert report.missing_fields['steps[].metrics.cached_tokens'] == 1


def test_metrics_without_a_supported_measurement_emit_no_usage_event():
    events, _ = convert(trajectory([agent_step(1, metrics=dict(cost_usd=0.5))]))
    assert usage(events) == []


def test_final_totals_are_never_added_to_per_step_usage():
    steps = [agent_step(1, metrics=dict(prompt_tokens=10, cached_tokens=0, completion_tokens=5)), agent_step(2, metrics=dict(prompt_tokens=20, cached_tokens=0, completion_tokens=7))]
    events, _ = convert(trajectory(steps, final_metrics=dict(total_prompt_tokens=30, total_completion_tokens=12, total_cached_tokens=0)))
    assert sum(row['output_tokens'] for row in usage(events)) == 12 and sum(row['input_tokens'] for row in usage(events)) == 30


def test_an_aggregated_step_is_flagged_because_its_per_call_split_is_unavailable():
    _, report = convert(trajectory([agent_step(1, llm_call_count=3, metrics=dict(prompt_tokens=10, cached_tokens=0, completion_tokens=5))]))
    assert report.warnings['aggregated_llm_calls_in_one_step'] == 1


def test_parent_and_subagent_usage_stay_in_separate_cases_so_it_is_not_counted_twice():
    child = trajectory([agent_step(1, metrics=dict(prompt_tokens=10, cached_tokens=0, completion_tokens=4))], trajectory_id='sub-1')
    parent = trajectory([agent_step(1, [call('c1')], [dict(source_call_id='c1', subagent_trajectory_ref=[dict(trajectory_id='sub-1')])],
        metrics=dict(prompt_tokens=10, cached_tokens=0, completion_tokens=4))], subagent_trajectories=[child])
    events, _ = convert(parent)
    totals = {}
    for row in usage(events):
        totals[row['case_id']] = totals.get(row['case_id'], 0) + row['output_tokens']
    assert sorted(totals.values()) == [4, 4]


# --- subagents and parent links -------------------------------------------------------------------------------------

def parent_with(children, refs, **root):
    results = [dict(source_call_id='c1', subagent_trajectory_ref=refs)]
    return trajectory([agent_step(1, [call('c1')], results)], subagent_trajectories=children, **root)


def child(ident, refs=None):
    steps = [agent_step(1, [call('k1')], [dict(source_call_id='k1', subagent_trajectory_ref=refs)])] if refs else [dict(step_id=1, source='user', message='m')]
    return trajectory(steps, trajectory_id=ident)


def test_an_embedded_subagent_gets_the_referencing_trajectory_as_its_declared_parent():
    events, report = convert(parent_with([child('sub-1')], [dict(trajectory_id='sub-1')]))
    root = events[0]['case_id']
    kids = [row for row in events if row['case_id'] != root]
    assert kids and {row['native_parent_thread_fingerprint'] for row in kids} == {root}
    assert 'subagent_parent_unknown' not in report.warnings


def test_an_unreferenced_embedded_subagent_has_no_parent_and_the_gap_is_reported():
    events, report = convert(parent_with([child('sub-1')], []))
    assert all('native_parent_thread_fingerprint' not in row for row in events) and report.warnings['subagent_parent_unknown'] == 1


def test_an_embedded_subagent_referenced_by_two_trajectories_has_an_ambiguous_parent():
    document = parent_with([child('sub-1', [dict(trajectory_id='sub-2')]), child('sub-2')], [dict(trajectory_id='sub-1'), dict(trajectory_id='sub-2')])
    events, report = convert(document)
    assert report.warnings['subagent_parent_ambiguous'] == 1
    second = next(row for row in events if row['case_id'] != events[0]['case_id'] and 'native_parent_thread_fingerprint' not in row)
    assert second['root_case_id'] == events[0]['root_case_id']


@pytest.mark.parametrize('children, refs', [
    ([child('a', [dict(trajectory_id='b')]), child('b', [dict(trajectory_id='a')])], [dict(trajectory_id='a')]),
    ([child('a', [dict(trajectory_id='a')])], [dict(trajectory_id='a')]),
    ([child('a', [dict(trajectory_id='b')]), child('b', [dict(trajectory_id='c')]), child('c', [dict(trajectory_id='a')])], [dict(trajectory_id='a')]),
], ids=['two-step', 'self', 'three-step'])
def test_a_cycle_among_subagent_references_is_rejected(children, refs):
    assert rejected(parent_with(children, refs)).reason == 'subagent_reference_cycle'


def test_a_reference_to_an_embedded_trajectory_that_does_not_exist_is_explicit_incomplete_evidence():
    events, report = convert(parent_with([], [dict(trajectory_id='missing')]))
    assert report.unsupported_features['unresolved_subagent_references'] == 1
    assert any(row.get('missing_reason') == 'subagent_trajectory_unresolved' for row in events)


def test_a_reference_by_file_path_is_counted_and_never_followed():
    events, report = convert(parent_with([], [dict(trajectory_path='/etc/passwd')]))
    assert report.unsupported_features['subagent_trajectory_path_references'] == 1 and '/etc/passwd' not in json.dumps(events) + json.dumps(report.to_dict())


def test_a_continuation_reference_is_counted_and_never_followed():
    _, report = convert(with_(trajectory(), lambda d: d.update(continued_trajectory_ref='../next.json')))
    assert report.unsupported_features['continuation_reference_not_followed'] == 1


@pytest.mark.parametrize('refs, reason', [([dict()], 'invalid_subagent_reference'), ([dict(session_id='only')], 'invalid_subagent_reference'),
    ([dict(trajectory_id='')], 'invalid_structure'), (['sub-1'], 'invalid_structure')], ids=['empty', 'session-only', 'empty-id', 'string'])
def test_a_malformed_subagent_reference_is_rejected(refs, reason):
    assert rejected(parent_with([], refs)).reason == reason


@pytest.mark.parametrize('children, reason', [
    ([trajectory(trajectory_id='dup'), trajectory(trajectory_id='dup')], 'duplicate_trajectory_id'),
    ([trajectory()], 'missing_field'),
    ([trajectory(trajectory_id='a', subagent_trajectories=[trajectory(trajectory_id='b')])], 'unsupported_nesting'),
    ([trajectory(trajectory_id='a', schema_version='ATIF-v1.7')], 'unsupported_schema_version'),
    (['text'], 'missing_field'),
], ids=['duplicate-ids', 'no-id', 'nested-embedding', 'embedded-older-version', 'not-an-object'])
def test_invalid_embedded_subagents_are_rejected(children, reason):
    assert rejected(with_(trajectory(), lambda d: d.update(subagent_trajectories=children))).reason == reason


def test_an_embedded_id_equal_to_the_root_id_is_a_duplicate():
    assert rejected(with_(trajectory(trajectory_id='same'), lambda d: d.update(subagent_trajectories=[trajectory(trajectory_id='same')]))).reason == 'duplicate_trajectory_id'


# --- round trip through the ledger ------------------------------------------------------------------------------------

def test_retained_metadata_survives_serialization_and_ledger_ingestion_unchanged(tmp_path):
    events, _ = convert(full())
    assert [event(json.loads(serialize(row))) for row in events] == events
    ledger = Ledger(tmp_path / 'ledger.jsonl')
    assert ledger.ingest(events) == len(events) and ledger.ingest(events) == 0
    stored = [json.loads(line) for line in (tmp_path / 'ledger.jsonl').read_text().splitlines()]
    assert sorted(stored, key=lambda row: (row['case_id'], row['event_id'])) == sorted(events, key=lambda row: (row['case_id'], row['event_id']))


def test_the_dropped_content_is_not_claimed_to_round_trip_in_the_report():
    assert convert(full())[1].to_dict()['round_trip'] == 'retained_metadata_only'
