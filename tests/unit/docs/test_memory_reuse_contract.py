"""The reuse contract matrix cites real modules and its synthetic fixtures are well formed."""
import hashlib
import json
from pathlib import Path
import re
import time

import pytest

from agent_harness_lab.evaluation.oracle_history import HistoryStore, bank_authority, oracle_case
from agent_harness_lab.sources.schema import EvidenceRecord, SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.telemetry.schema import event
from tools.diagnose import analyze

ROOT = Path(__file__).resolve().parents[3]
MATRIX = (ROOT / 'docs/memory-reuse-contract-matrix.md').read_text()
RATIONALE = (ROOT / 'docs/memory-reuse-rationale.md').read_text()
FIXTURE_PATH = ROOT / 'tests/support/memory_reuse_fixtures.json'
DATA = json.loads(FIXTURE_PATH.read_text())
CANARY = 'SYNTHETIC-CANARY-0000'
CASES = {'unchanged_rerun', 'changed_source_revision', 'changed_parser_or_algorithm', 'changed_grader_hash',
         'quarantined_oracle', 'changed_model_runtime', 'legacy_record_missing_provenance', 'interrupted_run',
         'secret_bearing_trace'}
STATES = {'active', 'quarantined', 'sealed', 'running', 'complete', 'missing'}


# Test-only reading of the dependency table; it is not planner code.
def stale_stages(changes):
    stale = set()
    for name, spec in DATA['stages'].items():
        if spec['kind'] != 'deterministic': continue
        if set(changes) & set(spec['identity']) or set(spec['depends_on']) & stale: stale.add(name)
    return stale


def test_fixture_file_is_well_formed():
    assert DATA['synthetic'] is True and DATA['schema_version'] == 1
    decisions, claims = set(DATA['vocabulary']['decisions']), set(DATA['vocabulary']['claims'])
    stages, baseline = DATA['stages'], DATA['baseline']
    for spec in stages.values():
        assert set(spec['depends_on']) <= set(stages)
        assert set(spec['identity']) <= set(baseline) | {'oracle_state'}
    ids = [fixture['id'] for fixture in DATA['fixtures']]
    assert len(ids) == len(set(ids))
    assert {fixture['covers'] for fixture in DATA['fixtures']} == CASES
    for fixture in DATA['fixtures']:
        assert set(fixture['changes']) <= set(baseline)
        assert all(fixture['changes'][key] != baseline[key] for key in fixture['changes'])
        assert fixture['description'] and fixture['expected']['claim'] in claims
        assert set(fixture['expected']['stages']) == set(stages)
        assert set(fixture['expected']['stages'].values()) <= decisions
        for path in fixture['evidence']: assert (ROOT / path).is_file(), path


def test_deterministic_expectations_follow_the_dependency_table():
    for fixture in DATA['fixtures']:
        if fixture['id'] == 'secret_bearing_trace': continue
        expected = fixture['expected']['stages']
        stale = stale_stages(fixture['changes'])
        for name, spec in DATA['stages'].items():
            if spec['kind'] == 'deterministic':
                assert expected[name] == ('recompute' if name in stale else 'reuse'), (fixture['id'], name)
        assert expected['fresh_trial'] == 'not_reusable'
    secret = next(f for f in DATA['fixtures'] if f['id'] == 'secret_bearing_trace')['expected']['stages']
    assert secret['capture_selection'] == 'excluded' and 'reuse' not in {secret[n] for n in ('normalization', 'event_analysis')}


def test_fixtures_hold_no_private_data():
    raw = FIXTURE_PATH.read_text()
    assert raw.count(CANARY) == 1
    stripped = raw.replace(CANARY, '')
    assert not re.search(r'/(home|Users|root|tmp|var)/|[A-Za-z]:\\|@[A-Za-z0-9-]+\.|https?://', stripped)
    assert not re.search(r'sk-[A-Za-z0-9]{8}|gh[pousr]_[A-Za-z0-9]{8}|AKIA[0-9A-Z]{8}|BEGIN [A-Z ]*PRIVATE KEY|[0-9a-f]{32}', stripped)
    values = [v for fixture in DATA['fixtures'] for v in fixture['changes'].values() if isinstance(v, str)]
    assert all(v.startswith('synthetic') or v in STATES for v in values)
    assert all(v.startswith('synthetic') or v in STATES for v in DATA['baseline'].values() if isinstance(v, str))


def test_documents_cite_existing_modules_and_every_fixture():
    for document in (MATRIX, RATIONALE):
        assert not re.search(r'/(home|Users)/', document)
    cited = set(re.findall(r'`((?:agent_harness_lab|tools|tests)/[A-Za-z0-9_/.-]+\.(?:py|json))`', MATRIX))
    assert len(cited) >= 20
    for path in cited: assert (ROOT / path).is_file(), path
    for fixture in DATA['fixtures']: assert f"`{fixture['id']}`" in MATRIX
    for decision in DATA['vocabulary']['decisions'] + DATA['vocabulary']['claims']: assert f'`{decision}`' in MATRIX


def test_history_enumeration_and_capacity_match_the_documented_bounds(tmp_path):
    root = tmp_path / 'history'; root.mkdir(mode=0o700)
    store = HistoryStore(root)
    with store.locked(create=True):
        for index in range(256):
            bundle = hashlib.sha256(str(index).encode()).hexdigest()
            case = store.new_case(oracle_case(bundle))
            store.append(case, [], 'oracle_registered', 'active', dict(bundle_sha256=bundle))
        assert len(store.case_ids(None)) == 256
        with pytest.raises(ValueError, match='case directory count bound exceeded'): store.case_ids(255)
        with pytest.raises(ValueError, match='history case capacity exhausted'):
            store.new_case(hashlib.sha256(b'one more').hexdigest())


def test_legacy_unbound_history_has_no_bank_authority_and_bound_history_requires_it():
    assert bank_authority({}) is None
    with pytest.raises(ValueError, match='explicit bank authority'): bank_authority({'bank_revision_sha256': 'x'})


def test_role_projection_is_a_fixed_allow_list(tmp_path):
    from agent_harness_lab.evaluation.construction import views
    assets = tmp_path / 'assets'
    for skill in ('harness-evaluation', 'harness-self-improvement'):
        (assets / 'skills' / skill).mkdir(parents=True); (assets / 'skills' / skill / 'SKILL.md').write_text('synthetic')
    data = dict(analysis=dict(events=1, findings=[dict(kind='k', status='s', explanation='e', evidence=['hidden'])]),
                paired_history_analysis=dict(status='blocked'), source_context=dict(hidden=True),
                incident_retrieval=dict(approval='caller_declared_reviewed_approval_unverified',
                                        results=[dict(incident_id='i1', authority='untrusted_evidence_only')]),
                evaluation_outcomes=['hidden'], sources=['hidden'])
    folders = views(data, assets, tmp_path / 'views', time.monotonic() + 60)
    summary = json.loads((folders['proposer'] / 'analysis-summary.json').read_text())
    assert summary['paired_history_analysis'] == dict(status='blocked')
    assert summary['incident_retrieval'] == data['incident_retrieval']
    assert 'untrusted evidence' in ' '.join(summary['limitations'])
    assert summary['findings'] == [dict(kind='k', status='s', explanation='e')]
    assert not {'source_context', 'evaluation_outcomes', 'sources'} & set(summary)


def test_analysis_is_deterministic_and_one_pass():
    rows = [dict(case_id=c, event_id=str(i), activity=a) for c in 'ab' for i, a in enumerate(('inspect', 'validate'), 1)]
    first, second = analyze(iter(rows), select_methods=True), analyze(iter(rows), select_methods=True)
    assert first == second
    assert set(first['method_execution']) >= {'directly_follows', 'recurring_sequences', 'duplicate_observations'}


def test_unchanged_snapshot_reuses_its_cursor_and_freshness_stays_unknown(tmp_path):
    source = SourceSpec('local_file', 'ahl-metadata', 'v1', digest('root'), digest('project'), digest('scope'),
        identity_provenance='source_declared_id', enabled=True)
    store = SourceStore(tmp_path / 'cache.json', project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
    record = EvidenceRecord(source.source_key, digest('native'), digest('revision'), digest('content'),
        'behavioral_event', 'synthetic', '2026-01-01T00:00:00Z')
    first = store.ingest(source, [record], snapshot_sha256=digest('snapshot-1'))
    again = store.ingest(source, [record], cursor_before=first.cursor_after_sha256, snapshot_sha256=digest('snapshot-1'))
    assert (again.imported, again.skipped, again.cursor_after_sha256) == (0, 1, first.cursor_after_sha256)
    changed = store.ingest(source, [record], cursor_before=first.cursor_after_sha256, snapshot_sha256=digest('snapshot-2'))
    assert changed.cursor_after_sha256 != first.cursor_after_sha256
    status = store.status(source)
    assert status['capture_completeness'] == 'unknown' and status['source_read'] is False


def test_event_envelope_drops_unlisted_fields_but_keeps_listed_text_verbatim():
    value = event(dict(case_id='c', event_id='e', activity='tool_call', free_text_body=CANARY, tool_name=CANARY))
    assert 'free_text_body' not in value and value['tool_name'] == CANARY
