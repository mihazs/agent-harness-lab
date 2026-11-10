"""Synthetic Hindsight knowledge-page metadata; no page bodies or service access."""
import contextlib
import io
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources import hindsight_knowledge
from agent_harness_lab.sources.hindsight_knowledge import HindsightKnowledge
from agent_harness_lab.sources.schema import SourceSpec, digest

INSTANCE, BANK = 'synthetic-instance', 'SYNTHETIC_PRIVATE_BANK'
SECRET = 'SYNTHETIC_PRIVATE_NAME'


@pytest.fixture
def source(tmp_path):
    archive = tmp_path/'archive'; archive.mkdir()
    manifest = dict(schema_version=1, source_bank_id=BANK, document_count=0, observation_count=0,
        invalidated_memory_count=0, knowledge_page_count=2)
    pages = [dict(id='SYNTHETIC_PRIVATE_FOLDER', parent_id=None, kind='folder', name=SECRET,
                  mental_model_id=None, managed=False, sort_order=0),
             dict(id='SYNTHETIC_PRIVATE_PAGE', parent_id='SYNTHETIC_PRIVATE_FOLDER', kind='page', name=SECRET,
                  mental_model_id='SYNTHETIC_PRIVATE_MODEL', managed=True, sort_order=1)]
    (archive/'manifest.json').write_text(json.dumps(manifest)); (archive/'knowledge_pages.json').write_text(json.dumps(pages))
    selection = dict(schema_version=1, hindsight_version='1.1.0', instance=INSTANCE, bank_id=BANK, archive='archive')
    spec = SourceSpec('local_file', 'hindsight-knowledge-pages', 'v1', digest(str(tmp_path.resolve())),
        digest(('hindsight.bank', INSTANCE, BANK)), digest(selection), identity_provenance='source_declared_id', enabled=True)
    (tmp_path/'selection.json').write_text(json.dumps(selection))
    declaration = tmp_path/'source.json'
    declaration.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    return archive, manifest, pages, selection, spec, declaration


def test_page_references_are_private_unverified_claims(source):
    _, _, _, _, spec, declaration = source
    connector = HindsightKnowledge(declaration); snapshot = connector.read(spec)
    folder, page = connector.claims
    assert folder.kind == 'folder' and page.kind == 'page' and page.parent_sha256 == folder.native_id_sha256
    assert page.backing_model_sha256 is not None and len(snapshot.records) == 3
    assert connector.summary['capture_completeness'] == 'unknown' and not connector.summary['task_outcomes_verified']
    assert all((r.causal_status, r.independence, r.capture_completeness) == ('unknown', 'unknown', 'partial') for r in snapshot.records)
    for secret in (SECRET, BANK, 'SYNTHETIC_PRIVATE_FOLDER', 'SYNTHETIC_PRIVATE_PAGE', 'SYNTHETIC_PRIVATE_MODEL'):
        assert secret not in repr((connector.claims, connector.summary, snapshot))


def test_wrong_bank_is_blocked_before_page_file_open(source):
    archive, manifest, _, _, spec, declaration = source
    manifest['source_bank_id'] = 'other'; (archive/'manifest.json').write_text(json.dumps(manifest))
    with patch('agent_harness_lab.sources.hindsight_knowledge.read_regular', wraps=hindsight_knowledge.read_regular) as read:
        with pytest.raises(PermissionError): HindsightKnowledge(declaration).read(spec)
    assert [call.args[0].name for call in read.call_args_list] == ['manifest.json']


@pytest.mark.parametrize('case', ['count', 'duplicate', 'unknown_field', 'bad_kind', 'bad_parent', 'bad_order', 'bad_managed'])
def test_invalid_page_metadata_fails_closed(source, case):
    archive, manifest, pages, _, spec, declaration = source
    if case == 'count': manifest['knowledge_page_count'] = 3
    if case == 'duplicate': pages[1]['id'] = pages[0]['id']
    if case == 'unknown_field': pages[1]['untrusted'] = SECRET
    if case == 'bad_kind': pages[1]['kind'] = SECRET
    if case == 'bad_parent': pages[1]['parent_id'] = True
    if case == 'bad_order': pages[1]['sort_order'] = True
    if case == 'bad_managed': pages[1]['managed'] = 'true'
    (archive/'manifest.json').write_text(json.dumps(manifest)); (archive/'knowledge_pages.json').write_text(json.dumps(pages))
    with pytest.raises(ValueError): HindsightKnowledge(declaration).read(spec)


def test_dangling_and_cyclic_parent_claims_do_not_become_verified_edges(source):
    archive, _, pages, _, spec, declaration = source
    pages[0]['parent_id'] = pages[0]['id']; pages[1]['parent_id'] = 'SYNTHETIC_PRIVATE_MISSING'
    (archive/'knowledge_pages.json').write_text(json.dumps(pages))
    connector = HindsightKnowledge(declaration); snapshot = connector.read(spec)
    assert connector.claims[0].parent_sha256 == connector.claims[0].native_id_sha256
    assert snapshot.graph is None and all(r.independence == 'unknown' for r in snapshot.records)
    assert 'parent_and_model_references_unverified' in connector.summary['warnings']


def test_same_page_identifier_in_different_bank_does_not_alias(source):
    archive, manifest, _, selection, spec, declaration = source
    first = HindsightKnowledge(declaration); first.read(spec)
    selection['bank_id'] = 'SYNTHETIC_PRIVATE_OTHER_BANK'; manifest['source_bank_id'] = selection['bank_id']
    (archive/'manifest.json').write_text(json.dumps(manifest))
    (declaration.parent/'selection.json').write_text(json.dumps(selection))
    spec = SourceSpec(**{**spec.to_dict(), 'scope_sha256': digest(selection),
        'project_sha256': digest(('hindsight.bank', INSTANCE, selection['bank_id']))})
    declaration.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    second = HindsightKnowledge(declaration); second.read(spec)
    assert first.claims[0].native_id_sha256 != second.claims[0].native_id_sha256


def test_page_timestamps_are_reported_and_invalid_times_remain_unknown(source):
    archive, _, pages, _, spec, declaration = source
    pages[0]['created_at'] = '2031-04-01T00:00:00Z'
    pages[0]['updated_at'] = '2031-05-01T00:00:00'
    pages[1]['created_at'] = '9999-12-31T23:59:59Z'
    (archive/'knowledge_pages.json').write_text(json.dumps(pages))
    connector = HindsightKnowledge(declaration); connector.read(spec)
    first, second = connector.claims
    assert first.created_at == '2031-04-01T00:00:00+00:00' and first.created_at_status == 'native'
    assert first.updated_at is None and first.updated_at_status == 'invalid'
    assert second.created_at is None and second.created_at_status == 'placeholder'


def test_file_mutation_and_symlink_refused(source, tmp_path):
    archive, _, _, _, spec, declaration = source
    with patch('agent_harness_lab.sources.hindsight_knowledge.regular_sha256', return_value='0'*64):
        with pytest.raises(ValueError): HindsightKnowledge(declaration).read(spec)
    target = archive/'knowledge_pages.json'; target.unlink(); target.symlink_to(tmp_path/'other')
    with pytest.raises((ValueError, OSError)): HindsightKnowledge(declaration).read(spec)


def test_cli_preview_import_is_offline_private_and_idempotent(source, tmp_path):
    _, _, _, _, spec, declaration = source
    cache = tmp_path/'cache.json'
    def invoke(operation):
        output = io.StringIO()
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(output):
            code = main(['sources', operation, '--manifest', str(declaration), '--project-sha256', spec.project_sha256,
                         '--scope-sha256', spec.scope_sha256, '--cache', str(cache)])
        assert SECRET not in output.getvalue() and BANK not in output.getvalue()
        return code, json.loads(output.getvalue())
    code, preview = invoke('preview')
    assert code == 0 and preview['hindsight_knowledge_summary']['capture_completeness'] == 'unknown'
    assert invoke('import')[0] == 0 and invoke('import')[1]['imported'] == 0
    assert SECRET not in cache.read_text() and BANK not in cache.read_text()
