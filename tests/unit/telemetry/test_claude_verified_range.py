"""Fixture-verified Claude Code release range; the fixtures hold structure only, with invented values."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

import pytest

from agent_harness_lab.telemetry import claude_native
from agent_harness_lab.telemetry.claude_native import NEWEST_VERIFIED, VERIFIED, VERIFIED_RANGES, expand, read
from tests.integration.telemetry.test_claude_native import ClaudeNativeTests  # noqa: F401 - importable builder for sibling tests

FIXTURES = Path(__file__).resolve().parents[2]/'fixtures'/'claude_contract'
PROJECT = Path('/synthetic/project')
LISTED = ('mode', 'atis-latch', 'api-request', 'api-request-shape', 'api-request-blob', 'tools-used', 'file-history-delta')
# Releases between two fixture-verified runs: no capture of them was ever read, so no fixture exists and none is accepted.
GAP = ('2.1.319', '2.1.320', '2.1.321')
# Kinds the verified adapter deliberately never interprets: identity, link, name, mode and lineage records. Counted per type, never parsed.
UNKNOWN_BY_DESIGN = {'agent-name', 'fork-context-ref', 'permission-mode', 'pr-link', 'session-owner'}


def rows(version):
    return [json.loads(line) for line in (FIXTURES/f'{version}.jsonl').read_text().splitlines()]


def write(tmp_path, records, name='capture.jsonl'):
    path = tmp_path/name; path.write_text(''.join(json.dumps(row)+'\n' for row in records)); return path


def shape(events):
    """What a repair needs per tool call, with no ids: ordered tool name, linked outcome and the content-free error flag."""
    calls = {e['call_id']: e for e in events if e['activity'] == 'tool_call'}
    return [(calls[e['call_id']]['tool_name'], e['execution_status'], e['capture_status'])
            for e in events if e['activity'] == 'tool_result']


def test_every_version_in_the_declared_range_has_a_contract_fixture_and_nothing_else_is_accepted():
    assert sorted(path.stem for path in FIXTURES.glob('*.jsonl')) == sorted(VERIFIED)
    assert NEWEST_VERIFIED == max(VERIFIED, key=claude_native.release) and claude_native.RUNTIME_VERSION in VERIFIED
    for low, high in VERIFIED_RANGES:
        assert all(version in VERIFIED for version in expand(low, high))
    for version in VERIFIED:  # a fixture names only its own release
        assert {row['version'] for row in rows(version) if 'version' in row} == {version}


def test_ranges_cannot_cross_a_minor_line_or_run_backwards():
    for bad in (('2.1.316', '2.2.0'), ('2.1.318', '2.1.316'), ('x', '2.1.1')):
        with pytest.raises(ValueError): expand(*bad)
    assert expand('2.1.316', '2.1.318') == ('2.1.316', '2.1.317', '2.1.318')


@pytest.mark.parametrize('version', VERIFIED)
def test_each_verified_release_yields_ordered_events_with_tool_names_call_ids_and_outcomes(tmp_path, version):
    events, summary, _ = read(write(tmp_path, rows(version)), PROJECT)
    assert summary['recorded_versions'] == [version]
    assert all(e['source_order_profile'] == claude_native.ORDER_PROFILE for e in events)
    order = [(e['source_order_segment'], e['capture_order'], e['source_suborder']) for e in events]
    assert order == sorted(order) and len(set(order)) == len(order)
    calls = [e for e in events if e['activity'] == 'tool_call']; results = [e for e in events if e['activity'] == 'tool_result']
    assert calls and all(e['tool_name'] in claude_native.KNOWN_TOOLS and e['call_id'] for e in calls)
    assert {e['call_id'] for e in calls} == {e['call_id'] for e in results}
    assert all(e['capture_status'] == 'linked' and e['tool_name'] for e in results)
    assert 'error' in {e['execution_status'] for e in results}  # is_error true is the only error signal; no text is read
    assert 'SYNTHETIC' not in json.dumps(events) and 'synthetic' not in json.dumps(events)


def test_the_current_release_matches_what_the_pinned_release_yields():
    for events in (read(FIXTURES/f'{version}.jsonl', PROJECT)[0] for version in ('2.1.316', '2.1.317', '2.1.318', '2.1.322')):
        assert {status for _, status, _ in shape(events)} >= {'error', 'returned'}
        assert {k for e in events for k in e} >= {'tool_name', 'call_id', 'execution_status', 'source_order_profile'}


def test_the_middle_release_counts_the_kinds_it_adds_as_unknown_with_a_typed_reason_and_drops_nothing(tmp_path):
    _, summary, _ = read(FIXTURES/'2.1.317.jsonl', PROJECT)
    unknown = {name for name, value in summary['skipped_entry_types'].items() if value['reason'] == 'unknown_entry_type'}
    assert unknown == {'session-owner', 'permission-mode', 'pr-link', 'agent-name'}
    assert {name for name, value in summary['skipped_entry_types'].items() if value['reason'] == 'declared_non_event_entry'} >= set(LISTED)
    assert summary['recorded_versions'] == ['2.1.317'] and sum(v['count'] for v in summary['skipped_entry_types'].values()) <= summary['unsupported_records']


def test_unknown_and_declared_non_event_entry_types_are_counted_with_a_typed_reason_never_dropped(tmp_path):
    records = rows('2.1.318')
    extra = [dict(type=name, sessionId='session-001', payload='SYNTHETIC_SECRET') for name in ('future-kind', 'future-kind', *LISTED[:2])]
    extra.append(dict(type='SYNTHETIC SECRET name', sessionId='session-001'))
    extra.append(dict(type=None))
    _, summary, _ = read(write(tmp_path, records[:3]+extra+records[3:]), PROJECT)
    skipped = summary['skipped_entry_types']
    assert skipped['future-kind'] == dict(count=2, reason='unknown_entry_type')
    assert skipped['mode'] == dict(count=1, reason='declared_non_event_entry') and skipped['unrecognized']['reason'] == 'unknown_entry_type'
    assert skipped['unrecognized']['count'] == 2
    assert sum(v['count'] for v in skipped.values()) <= summary['unsupported_records']
    assert 'SYNTHETIC' not in json.dumps(summary)
    def kept(events): return [{k: v for k, v in e.items() if k != 'capture_order'} for e in events]  # skipped lines still take a position
    assert kept(read(write(tmp_path, records[:3]+extra+records[3:]), PROJECT)[0]) == kept(read(write(tmp_path, records, 'baseline.jsonl'), PROJECT)[0])


def test_the_issue_listed_record_kinds_are_all_typed_in_a_synthetic_store(tmp_path):
    records = rows('2.1.318')+[dict(type=name, sessionId='session-001') for name in (*LISTED, 'file-history-snapshot', 'ai-title', 'queue-operation')]
    _, summary, _ = read(write(tmp_path, records), PROJECT)
    assert {name for name, value in summary['skipped_entry_types'].items() if value['reason'] == 'declared_non_event_entry'} >= set(LISTED)
    assert not [name for name, value in summary['skipped_entry_types'].items() if value['reason'] == 'unknown_entry_type']


def test_unknown_extra_fields_on_known_types_are_tolerated_and_change_nothing(tmp_path):
    records = rows('2.1.318')
    widened = [dict(row, futureField='SYNTHETIC_SECRET', nested=dict(deep=1)) if row['type'] in claude_native.ENTRY_TYPES else row for row in records]
    for row in widened:
        message = row.get('message')
        if isinstance(message, dict):
            message['futureKey'] = 1
            for block in message['content'] if isinstance(message['content'], list) else ():
                block['futureBlockKey'] = 'SYNTHETIC_SECRET'
    assert read(write(tmp_path, widened), PROJECT)[0] == read(write(tmp_path, records, 'plain.jsonl'), PROJECT)[0]


def test_a_release_outside_the_range_or_mixed_into_a_capture_is_never_parsed_as_verified(tmp_path):
    for version in ('2.1.315', *GAP, '2.1.323', '2.2.0', '3.0.0'):
        records = [dict(row, version=version) if 'version' in row else row for row in rows('2.1.318')]
        with pytest.raises(ValueError, match='unsupported native Claude version'): read(write(tmp_path, records), PROJECT)
    mixed = rows('2.1.318'); mixed[-1] = dict(mixed[2], version='2.1.319')
    with pytest.raises(ValueError): read(write(tmp_path, mixed, 'mixed.jsonl'), PROJECT)


def test_a_capture_may_resume_across_verified_releases_and_keeps_distinct_runtime_fingerprints(tmp_path):
    records = rows('2.1.318')
    resumed = [dict(row, version='2.1.316') if row.get('type') == 'assistant' else row for row in records]
    events, summary, _ = read(write(tmp_path, resumed), PROJECT)
    assert summary['recorded_versions'] == ['2.1.316', '2.1.318'] and len({e['runtime_fingerprint'] for e in events}) == 2


def test_skipped_type_counts_aggregate_across_files_in_discovery_summaries(tmp_path):
    counted = Counter({'future-kind': 2, 'mode': 1})
    assert claude_native.skipped_types(counted) == {'future-kind': dict(count=2, reason='unknown_entry_type'),
        'mode': dict(count=1, reason='declared_non_event_entry')}


def test_the_current_release_is_verified_by_its_own_fixture_and_the_releases_before_it_stay_unverified():
    assert '2.1.322' in VERIFIED and NEWEST_VERIFIED == '2.1.322' and (FIXTURES/'2.1.322.jsonl').is_file()
    for version in GAP: assert version not in VERIFIED and not (FIXTURES/f'{version}.jsonl').exists()


def test_the_current_release_yields_ordered_events_with_tool_names_call_ids_and_outcomes(tmp_path):
    events, summary, _ = read(FIXTURES/'2.1.322.jsonl', PROJECT)
    calls = [e for e in events if e['activity'] == 'tool_call']; results = [e for e in events if e['activity'] == 'tool_result']
    assert summary['recorded_versions'] == ['2.1.322'] and calls and {e['call_id'] for e in calls} == {e['call_id'] for e in results}
    assert all(e['tool_name'] in claude_native.KNOWN_TOOLS and e['call_id'] for e in calls) and {e['execution_status'] for e in results} >= {'error', 'returned'}


def classify(version):
    """Handled kinds produce events, ignored ones are declared non-event bookkeeping, the rest are unknown by design."""
    skipped = read(FIXTURES/f'{version}.jsonl', PROJECT)[1]['skipped_entry_types']
    return {label: 'handled' if label in claude_native.ENTRY_TYPES else 'ignored' if skipped[label]['reason'] == 'declared_non_event_entry' else 'unknown-by-design'
        for label in {row['type'] for row in rows(version)}}


@pytest.mark.parametrize('version', ('2.1.317', '2.1.322'))
def test_every_entry_type_in_a_fixture_is_handled_ignored_or_unknown_by_design(version):
    classes = classify(version)
    assert {label for label, kind in classes.items() if kind == 'unknown-by-design'} == UNKNOWN_BY_DESIGN - ({'fork-context-ref'} if version == '2.1.317' else set())
    assert {label for label, kind in classes.items() if kind == 'ignored'} >= set(LISTED) | {'cost-state', 'ai-title'}
    assert {label for label, kind in classes.items() if kind == 'handled'} == {'user', 'assistant', 'attachment', 'system'}


def test_unknown_by_design_kinds_change_no_event_and_never_become_an_outcome(tmp_path):
    records = rows('2.1.322')
    without = [row for row in records if row['type'] not in UNKNOWN_BY_DESIGN]
    def kept(path): return [{k: v for k, v in e.items() if k != 'capture_order'} for e in read(path, PROJECT)[0]]  # skipped lines still take a position
    assert kept(write(tmp_path, records)) == kept(write(tmp_path, without, 'without.jsonl'))
    summary = read(write(tmp_path, records), PROJECT)[1]
    assert {label for label, value in summary['skipped_entry_types'].items() if value['reason'] == 'unknown_entry_type'} == UNKNOWN_BY_DESIGN


IMAGE = dict(type='image', source=dict(type='base64', media_type='synthetic', data='SYNTHETIC_SECRET'))


@pytest.mark.parametrize('block', (IMAGE, dict(type='image'), dict(type=None), {}, dict(type='future-block')),
    ids=('image', 'image-without-source', 'null-type', 'no-type', 'future-block'))
def test_a_content_block_the_adapter_cannot_represent_is_one_unsupported_coverage_event_and_never_an_outcome(tmp_path, block):
    records = rows('2.1.322')
    holder = next(row for row in records if row['type'] == 'user' and isinstance(row['message']['content'], list) and any(b.get('type') == 'image' for b in row['message']['content']))
    plain = deepcopy(records); next(row for row in plain if row.get('uuid') == holder['uuid'])['message']['content'] = [b for b in holder['message']['content'] if b.get('type') != 'image']
    holder['message']['content'] = [b if b.get('type') != 'image' else block for b in holder['message']['content']]
    events, summary, _ = read(write(tmp_path, records), PROJECT); baseline, base_summary, _ = read(write(tmp_path, plain, 'plain.jsonl'), PROJECT)
    extra = [e for e in events if e['activity'] == 'capture_coverage' and e.get('missing_reason') == 'unsupported_native_block']
    assert len(extra) == 1 and not any(key in extra[0] for key in ('execution_status', 'tool_name', 'call_id', 'message_fingerprint'))
    assert summary['unsupported_records'] == base_summary['unsupported_records']+1 and len(events) == len(baseline)+1
    assert 'SYNTHETIC_SECRET' not in json.dumps(events)
