"""Size and time bounds for analysis.json and the event store (#666), built only from synthetic data.

Histories are generated in-test. Large streams use reduced limit parameters so the test stays fast while
still proving that written artifacts stay under the documented bound however many events pass through.
"""
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_harness_lab.diagnostics import bounds
from agent_harness_lab.evaluation import construction
from agent_harness_lab.telemetry.ledger import Ledger
from agent_harness_lab.telemetry.schema import event, message_fingerprint, serialize
from agent_harness_lab.workflow import write_json
from tests.integration.diagnostics.test_analysis_pipeline import Pipeline
from tests.integration.diagnostics.test_reported_actionable_analysis import SMALL, history
from agent_harness_lab.cli import assets

ROOT = Path(__file__).resolve().parents[3]
EVENTS = 300_000
LIMITS = dict(analysis_bytes=200_000, detail_rows=100, evidence_refs=5, store_bytes=150_000, store_seconds=60)


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    for name, folder in (('CODEX_HOME', 'codex'), ('CLAUDE_CONFIG_DIR', 'claude'), ('XDG_STATE_HOME', 'state')):
        monkeypatch.setenv(name, str(tmp_path/folder))
    return Pipeline(tmp_path)


def lines(count):
    for number in range(count):
        yield serialize(dict(case_id=f'SYN_{number % 997}', event_id=f'e{number}', activity='tool_call', capture_order=number,
                             tool_name='Bash', execution_status='error'))  # the collector's own line format


def test_event_store_stays_under_its_byte_bound_for_hundreds_of_thousands_of_events(tmp_path):
    args, report = SimpleNamespace(_artifact_limits=LIMITS), {}
    store = tmp_path/'events.v2.jsonl'
    Ledger(store).ingest(bounds.store_rows(lines(EVENTS), args, report))
    assert 0 < store.stat().st_size <= LIMITS['store_bytes']
    truncated = report['truncated']
    assert (truncated['applied'], truncated['reason']) == (True, 'byte_limit')
    assert report['events_written'] + truncated['events_omitted'] == EVENTS
    assert sum('capture_coverage' not in line for line in store.read_text().splitlines()) == report['events_written']
    assert not re.search(r'success|independen', json.dumps(report))


def test_truncated_store_ends_with_unknown_coverage_so_receipts_stay_unknown(tmp_path):
    from agent_harness_lab.evaluation.skills import activation_receipt

    def rows():
        yield serialize(dict(case_id='C1', event_id='s0', activity='tool_call', tool_name='Skill', skill_id='skill-a', capture_complete=True,
                             skill_signal='declared_invocation', skill_signal_confidence='declared_name', capture_order=0))
        for number in range(1, 200):
            yield serialize(dict(case_id='C1', event_id=f'e{number}', activity='tool_call', capture_complete=True, capture_order=number))
    complete = [json.loads(line) for line in rows()]
    assert activation_receipt(complete[1:], case_id='C1', skill_id='skill-a')['state'] == 'unknown'  # no signal seen
    complete[0]['skill_id'] = 'skill-b'
    assert activation_receipt(complete, case_id='C1', skill_id='skill-a')['state'] == 'not_observed'
    store, report = tmp_path/'events.v2.jsonl', {}
    Ledger(store).ingest(bounds.store_rows(rows(), SimpleNamespace(_artifact_limits=dict(LIMITS, store_bytes=8000)), report))
    kept = [json.loads(line) for line in store.read_text().splitlines()]
    assert report['truncated']['applied'] and store.stat().st_size <= 8000
    assert (kept[-1]['activity'], kept[-1]['capture_complete'], kept[-1]['missing_reason']) == ('capture_coverage', False, 'event_store_truncated')
    kept[0]['skill_id'] = 'skill-b'
    assert activation_receipt(kept, case_id='C1', skill_id='skill-a')['state'] == 'unknown'
    untruncated = {}
    assert not any(r['activity'] == 'capture_coverage' for r in bounds.store_rows(rows(), SimpleNamespace(_artifact_limits=LIMITS), untruncated))


def test_event_store_time_bound_and_full_detail_opt_in(monkeypatch):
    clock = iter(range(10**6))
    monkeypatch.setattr(bounds, 'time', SimpleNamespace(monotonic=lambda: next(clock)))  # one tick per call
    slow, report = SimpleNamespace(_artifact_limits=dict(LIMITS, store_seconds=5)), {}
    written = [r for r in bounds.store_rows(lines(50), slow, report) if r['activity'] != 'capture_coverage']
    assert 0 < len(written) < 50 and report['truncated']['reason'] == 'time_limit' and report['truncated']['events_omitted'] == 50-len(written)
    whole = {}
    assert len(list(bounds.store_rows(lines(500), SimpleNamespace(_artifact_limits=LIMITS, full_detail=True), whole))) == 500
    assert whole['truncated'] == dict(applied=False, reason=None, events_omitted=0) and whole['full_detail'] is True


def synthetic_data(findings, sequences, refs):
    analysis = dict(events=EVENTS, cases=997, activities={'tool_call': EVENTS}, field_coverage={},
                    findings=[dict(kind=('tool_error', 'duplicate_observation')[number % 2], status='observed_pattern',
                                   evidence=[dict(case_id=f'SYN_{number % 997}', event_id=f'e{number}')], explanation='Structured observation.')
                              for number in range(findings)],
                    recurring_sequences=[dict(activities=['a', 'b', str(number)], reported_roots=3,
                                              evidence=[dict(case_id='SYN', event_id=f'e{n}') for n in range(refs)]) for number in range(sequences)],
                    process_mining=dict(raw_edges=[dict(source='a', target=str(n), occurrences=1, root_cases=1) for n in range(2000)],
                                        projected_run_collapsed_edges=[]))
    return dict(analysis=analysis, caveats=[], sources=[])


def test_analysis_artifact_stays_under_its_byte_bound_and_reports_counts_only(tmp_path):
    data = synthetic_data(EVENTS, 2000, 400)
    args = SimpleNamespace(_artifact_limits=LIMITS)
    view = bounds.analysis_view(data, args, {})
    path = tmp_path/'analysis.json'
    write_json(path, view)
    assert path.stat().st_size <= LIMITS['analysis_bytes']
    cut = view['artifact_bounds']['truncated']
    assert cut['applied'] and cut['sections']['findings.tool_error'] == dict(total=EVENTS//2, kept=LIMITS['detail_rows'])
    assert cut['sections']['recurring_sequences'] == dict(total=2000, kept=LIMITS['detail_rows'])
    assert cut['sections']['recurring_sequences.evidence_refs'] == LIMITS['detail_rows']*(400-LIMITS['evidence_refs'])
    assert view['analysis']['recurring_sequences'][0]['evidence_total'] == 400 and 'exceeds_byte_limit' not in view['artifact_bounds']
    assert view['artifact_bounds']['analysis_computed_from'] == 'all_events' and view['analysis']['detail_totals']['findings'] == EVENTS
    # The input is never mutated: receipts and the brief still see every row.
    assert len(data['analysis']['findings']) == EVENTS and len(data['analysis']['recurring_sequences'][0]['evidence']) == 400
    assert not re.search(r'success|independen', json.dumps(cut))


def test_row_limit_halves_until_the_file_fits_and_an_unfittable_remainder_is_reported():
    data = synthetic_data(5000, 0, 0)
    tight = SimpleNamespace(_artifact_limits=dict(LIMITS, detail_rows=4000, analysis_bytes=60_000))
    view = bounds.analysis_view(data, tight, {})
    assert bounds.size(view) <= 60_000 and view['artifact_bounds']['row_limit'] < 4000
    impossible = bounds.analysis_view(data, SimpleNamespace(_artifact_limits=dict(LIMITS, analysis_bytes=1000)), {})
    assert impossible['artifact_bounds']['exceeds_byte_limit'] is True and impossible['analysis']['findings'] == []
    full = bounds.analysis_view(data, SimpleNamespace(_artifact_limits=LIMITS, full_detail=True), {})
    assert len(full['analysis']['findings']) == 5000 and full['artifact_bounds']['truncated']['applied'] is False


def run(pipeline, monkeypatch, rows, *extra, **limits):
    monkeypatch.setitem(bounds.DEFAULTS, 'detail_rows', limits.get('detail_rows', bounds.DEFAULTS['detail_rows']))
    monkeypatch.setitem(bounds.DEFAULTS, 'store_bytes', limits.get('store_bytes', bounds.DEFAULTS['store_bytes']))
    import shutil
    shutil.rmtree(pipeline.root/'output', ignore_errors=True)
    code, receipt, _ = pipeline.invoke('analyze', rows, extra)
    assert code == 0
    output = pipeline.root/'output'
    return output, json.loads((output/'analysis-brief.json').read_text()), receipt


def test_pipeline_bounds_artifacts_while_brief_and_receipt_are_cap_independent(pipeline, monkeypatch):
    rows = history(SMALL*40)
    capped, capped_brief, capped_receipt = run(pipeline, monkeypatch, rows, detail_rows=3, store_bytes=4000)
    assert (capped/'events.v2.jsonl').stat().st_size <= 4000
    provenance = json.loads((capped/'analysis.json').read_text())['artifact_bounds']
    assert provenance['truncated']['applied'] and provenance['event_store']['truncated']['reason'] == 'byte_limit'
    assert provenance['truncated']['sections']['findings.tool_error']['kept'] == 3
    assert 'Detail below is capped' in (capped/'report.md').read_text()
    capped_size = (capped/'analysis.json').stat().st_size
    full, full_brief, full_receipt = run(pipeline, monkeypatch, rows, '--full-detail', detail_rows=3, store_bytes=4000)
    assert len((full/'events.v2.jsonl').read_text().splitlines()) == len(rows)
    assert json.loads((full/'analysis.json').read_text())['artifact_bounds']['truncated']['applied'] is False
    assert (full/'analysis.json').stat().st_size > 3*capped_size
    for key in ('mechanisms', 'weak_outcome_signals', 'coverage', 'methods'):
        assert capped_brief[key] == full_brief[key], key
    assert capped_brief['bounds']['artifacts'] == dict(analysis_detail_truncated=True, event_store_truncated=True)
    assert full_brief['bounds']['artifacts'] == dict(analysis_detail_truncated=False, event_store_truncated=False)
    assert any(row['id'] == 'full_detail_artifacts' and '--full-detail' in row['command'] for row in capped_brief['missing_evidence'])
    assert capped_receipt['analysis'] == full_receipt['analysis'] and capped_receipt['findings'] == full_receipt['findings']


def test_documented_bounds_match_the_enforced_defaults():
    text = re.sub(r'\s+', ' ', (ROOT/'docs/analysis.md').read_text())
    assert 'is not yet capped' not in text and '--full-detail' in text
    assert f"{bounds.DEFAULTS['analysis_bytes'] >> 20} MiB rendered" in text and f"at most {bounds.DEFAULTS['detail_rows']} rows" in text
    assert f"{bounds.DEFAULTS['store_bytes'] >> 20} MiB and {bounds.DEFAULTS['store_seconds']} seconds" in text


def test_controlled_role_views_receive_the_brief_and_keep_complete_summaries(tmp_path):
    data = synthetic_data(8*bounds.DEFAULTS['detail_rows'], 0, 0)
    brief = dict(profile='ahl-analysis-brief-v1', mechanisms=[dict(mechanism_id='Bash/tool_reported_error')])
    views = construction.views(data, assets(), tmp_path/'views', time.monotonic() + 10, brief=brief)
    for folder in views.values():
        assert json.loads((folder/'analysis-brief.json').read_text()) == brief
        summary = json.loads((folder/'analysis-summary.json').read_text())
        assert len(summary['findings']) == 8*bounds.DEFAULTS['detail_rows'] and 'truncated' not in summary
    absent = construction.views(data, assets(), tmp_path/'plain', time.monotonic() + 10)
    assert not (absent['proposer']/'analysis-brief.json').exists()


@pytest.mark.parametrize('role', ['propose', 'author'])
def test_controlled_role_prompts_point_at_the_brief_only_when_it_is_supplied(tmp_path, monkeypatch, role):
    seen = []

    def stop(client, backend, workspace, inputs, name, instructions, prompt, deadline):
        seen.append(instructions)
        raise RuntimeError('stop')
    monkeypatch.setattr(construction, 'run_role', stop)
    for supplied in (True, False):
        context = tmp_path/f'context-{supplied}'; context.mkdir()
        if supplied:
            (context/'analysis-brief.json').write_text('{}')
        arguments = [None, None, tmp_path/'baseline', context, tmp_path/f'work-{role}-{supplied}', tmp_path/f'out-{role}-{supplied}', time.monotonic() + 10]
        with pytest.raises(RuntimeError):
            (construction.propose if role == 'propose' else construction.author)(*arguments, analysis=True)
    assert 'analysis-brief.json' in seen[0] and 'not diagnoses' in seen[0] and 'analysis-brief.json' not in seen[1]


def test_message_fingerprint_is_content_free_structural_and_validated():
    text = 'Please, FIX the failing build now!'
    first = message_fingerprint(text, 'case-a')
    assert re.fullmatch(r'[0-9a-f]{16}', first) and first == message_fingerprint('please fix the failing   build now', 'case-a')
    assert first != message_fingerprint(text, 'case-b') and first != message_fingerprint('now fix the failing build please', 'case-a')
    assert message_fingerprint('yes ok', 'case-a') is None
    assert text.split()[1].lower() not in first
    base = dict(case_id='c', event_id='e', activity='user_message')
    assert event(dict(base, message_fingerprint=first))['message_fingerprint'] == first
    for bad in (dict(base, message_fingerprint='free text pasted here'), dict(base, activity='tool_call', message_fingerprint=first)):
        with pytest.raises(ValueError, match='message fingerprint'):
            event(bad)


def with_fingerprints(rows, same):
    count = {}
    result = []
    for row in rows:
        if row['activity'] == 'user_message':
            number = count[row['case_id']] = count.get(row['case_id'], 0) + 1
            text = 'please fix the failing build now' if same else f'please fix the failing build number {number}'
            row = dict(row, message_fingerprint=message_fingerprint(text, row['case_id']))
        result.append(row)
    return result


def test_restated_instructions_are_observed_only_from_repeated_fingerprints(pipeline, monkeypatch):
    signal = lambda brief: next(row for row in brief['weak_outcome_signals'] if row['id'] == 'restated_instructions')
    _, brief, _ = run(pipeline, monkeypatch, with_fingerprints(history(SMALL), True))
    row = signal(brief)
    assert (row['status'], row['count']) == ('observed_pattern', SMALL) and row['competing_explanations']
    assert not any(item['id'] == 'user_message_fingerprints' for item in brief['missing_evidence'])
    output, brief, _ = run(pipeline, monkeypatch, with_fingerprints(history(SMALL), False))
    assert (signal(brief)['status'], signal(brief)['count']) == ('not_observed_capture_unverified', 0)
    assert 'please fix' not in (output/'events.v2.jsonl').read_text() + (output/'analysis-brief.json').read_text()
    _, brief, _ = run(pipeline, monkeypatch, history(SMALL))
    assert signal(brief)['status'] == 'unknown' and any(item['id'] == 'user_message_fingerprints' for item in brief['missing_evidence'])
