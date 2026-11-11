"""Synthetic, caller-supplied MemPalace knowledge-graph results; no worker or backend access."""
import contextlib
import io
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources.mempalace import PIN
from agent_harness_lab.sources.mempalace_kg import MemPalaceKnowledgeGraph
from agent_harness_lab.sources.schema import SourceSpec, digest

INSTANCE, ENTITY = 'synthetic-instance', 'SYNTHETIC_PRIVATE_ENTITY'
SECRET = 'SYNTHETIC_PRIVATE_OBJECT'


def fact(direction='outgoing', obj=SECRET, start='2026-01-01', end=None, **extra):
    row = dict(direction=direction, subject=ENTITY, predicate='likes', object=obj, valid_from=start, valid_to=end,
        confidence=0.9, source_closet='SYNTHETIC_PRIVATE_CLOSET', current=end is None)
    if direction == 'incoming': row.update(subject=obj, object=ENTITY)
    return {**row, **extra}


@pytest.fixture
def source(tmp_path):
    # One ended and one current fact for the same triple: superseded history must stay distinct.
    facts = [fact(end='2026-02-01T00:00:00Z'), fact(start='2026-02-01T00:00:00Z'), fact('incoming', 'SYNTHETIC_PRIVATE_OTHER')]
    payload = dict(entity=ENTITY, as_of=None, facts=facts, count=3)
    export = tmp_path/'kg.json'; export.write_text(json.dumps(payload))
    entity = digest(('mempalace.kg_entity', INSTANCE, ENTITY))
    selection = dict(schema_version=1, upstream_commit=PIN, instance=INSTANCE, export='kg.json', entity_sha256=entity, as_of=None, direction='both')
    spec = SourceSpec('local_file', 'mempalace-kg', 'v1', digest(str(tmp_path.resolve())), entity, digest(selection),
        identity_provenance='source_declared_id', enabled=True)
    (tmp_path/'selection.json').write_text(json.dumps(selection))
    manifest = tmp_path/'source.json'
    manifest.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    return export, payload, selection, spec, manifest


def test_facts_are_private_unverified_and_history_stays_distinct(source):
    _, _, _, spec, manifest = source
    connector = MemPalaceKnowledgeGraph(manifest); snapshot = connector.read(spec)
    assert len(connector.claims) == 3 and len({c.native_id_sha256 for c in connector.claims}) == 3
    assert (connector.summary['current'], connector.summary['ended']) == (2, 1)
    assert connector.summary['producer_version'] == 'unverified' and not connector.summary['task_outcomes_verified']
    assert 'not_wing_scoped' in connector.summary['warnings']
    assert all((r.causal_status, r.independence, r.capture_completeness) == ('unknown', 'unknown', 'partial') for r in snapshot.records)
    for secret in (SECRET, ENTITY, 'SYNTHETIC_PRIVATE_CLOSET', 'SYNTHETIC_PRIVATE_OTHER'):
        assert secret not in repr((connector.claims, connector.summary, snapshot))


@pytest.mark.parametrize('case', ['other_entity', 'bad_direction', 'wrong_selected_direction', 'current_conflict', 'bad_date', 'bad_confidence',
                                  'duplicate', 'bad_count', 'as_of_mismatch', 'error', 'control_char', 'too_many'])
def test_bad_scope_or_inconsistent_result_fails_closed(source, case):
    export, payload, selection, spec, manifest = source
    first = payload['facts'][0]
    if case == 'other_entity': first['subject'] = 'other'
    if case == 'bad_direction': first['direction'] = 'sideways'
    if case == 'wrong_selected_direction':
        selection['direction'] = 'outgoing'
        (manifest.parent/'selection.json').write_text(json.dumps(selection))
        spec = SourceSpec(**{**spec.__dict__, 'scope_sha256': digest(selection)})
        manifest.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    if case == 'current_conflict': first['current'] = True
    if case == 'bad_date': first['valid_from'] = '2026-13-45'
    if case == 'bad_confidence': first['confidence'] = 2
    if case == 'duplicate': payload['facts'][1] = dict(first)
    if case == 'bad_count': payload['count'] = 2
    if case == 'as_of_mismatch': payload['as_of'] = '2026-01-01'
    if case == 'error': payload['error'] = 'boom'
    if case == 'control_char': first['predicate'] = 'a\nb'
    if case == 'too_many': payload['facts'] = [fact(obj=f'o{i}') for i in range(1001)]; payload['count'] = 1001
    export.write_text(json.dumps(payload))
    with pytest.raises(ValueError): MemPalaceKnowledgeGraph(manifest).read(spec)


def test_empty_result_and_unsupported_pin_fail_honestly(source):
    export, payload, selection, spec, manifest = source
    payload.update(facts=[], count=0); export.write_text(json.dumps(payload))
    connector = MemPalaceKnowledgeGraph(manifest); connector.read(spec)
    assert not connector.claims and connector.summary['capture_completeness'] == 'unknown'
    selection['upstream_commit'] = '0'*40
    (manifest.parent/'selection.json').write_text(json.dumps(selection))
    with pytest.raises(ValueError): MemPalaceKnowledgeGraph(manifest).read(spec)


def test_file_change_symlink_and_nesting_are_refused(source, tmp_path):
    export, _, _, spec, manifest = source
    with patch('agent_harness_lab.sources.mempalace_kg.regular_sha256', return_value='0'*64):
        with pytest.raises(ValueError): MemPalaceKnowledgeGraph(manifest).read(spec)
    export.write_text('{"a":'+'['*20+'0'+']'*20+'}')
    with pytest.raises(ValueError): MemPalaceKnowledgeGraph(manifest).read(spec)
    export.unlink(); export.symlink_to(tmp_path/'other')
    with pytest.raises((ValueError, OSError)): MemPalaceKnowledgeGraph(manifest).read(spec)


def test_cli_import_no_network_and_idempotent(source, tmp_path):
    _, _, _, spec, manifest = source
    cache = tmp_path/'cache.json'
    def invoke(operation, *extra):
        output = io.StringIO()
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(output):
            code = main(['sources', operation, '--manifest', str(manifest), '--project-sha256', spec.project_sha256,
                         '--scope-sha256', spec.scope_sha256, *extra])
        for secret in (SECRET, ENTITY): assert secret not in output.getvalue()
        return code, json.loads(output.getvalue())
    code, preview = invoke('preview')
    assert code == 0 and preview['mempalace_kg_summary']['returned'] == 3
    assert invoke('import', '--cache', str(cache))[0] == 0
    assert invoke('import', '--cache', str(cache))[1]['imported'] == 0
    assert SECRET not in cache.read_text() and ENTITY not in cache.read_text()
