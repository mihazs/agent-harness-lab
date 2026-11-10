"""Synthetic reported Hindsight model references; no live source or verified edges."""
import contextlib
import io
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources import hindsight_knowledge
from agent_harness_lab.sources.hindsight_model_refs import HindsightModelReferences
from agent_harness_lab.sources.schema import SourceSpec, digest

INSTANCE, BANK = 'synthetic-instance', 'SYNTHETIC_PRIVATE_BANK'
SECRET = 'SYNTHETIC_PRIVATE_CONTENT'


@pytest.fixture
def source(tmp_path):
    archive = tmp_path/'archive'; archive.mkdir()
    manifest = dict(schema_version=1, source_bank_id=BANK, document_count=0, observation_count=0,
        invalidated_memory_count=0, mental_model_count=1, knowledge_page_count=0)
    rows = [dict(id='SYNTHETIC_PRIVATE_MODEL', bank_id=BANK, content=SECRET,
        reflect_response=dict(based_on=dict(memories=[dict(id='SYNTHETIC_PRIVATE_MEMORY', text=SECRET),
            dict(id='SYNTHETIC_PRIVATE_MEMORY', text=SECRET), dict(id=None, text=SECRET)],
            mental_models=[], directives=[dict(id='SYNTHETIC_PRIVATE_DIRECTIVE', content=SECRET)])))]
    (archive/'manifest.json').write_text(json.dumps(manifest)); (archive/'mental_models.json').write_text(json.dumps(rows))
    selection = dict(schema_version=1, hindsight_version='1.1.0', instance=INSTANCE, bank_id=BANK, archive='archive')
    spec = SourceSpec('local_file', 'hindsight-model-references', 'v1', digest(str(tmp_path.resolve())),
        digest(('hindsight.bank', INSTANCE, BANK)), digest(selection), identity_provenance='source_declared_id', enabled=True)
    (tmp_path/'selection.json').write_text(json.dumps(selection))
    declaration = tmp_path/'source.json'
    declaration.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    return archive, manifest, rows, selection, spec, declaration


def test_reported_references_are_private_not_verified_edges(source):
    _, _, _, _, spec, declaration = source
    connector = HindsightModelReferences(declaration); snapshot = connector.read(spec)
    refs = [c for c in connector.claims if c.claim_type == 'model_reference']
    coverage, = [c for c in connector.claims if c.claim_type == 'model_reference_coverage']
    assert len(refs) == 4 and refs[0].target_sha256 == refs[1].target_sha256
    assert refs[0].native_id_sha256 != refs[1].native_id_sha256
    assert refs[2].target_sha256 is None and refs[2].target_identity == 'missing'
    assert (coverage.memories, coverage.mental_models, coverage.directives) == ('reported_list',)*3
    assert snapshot.graph is None and all(r.independence == 'unknown' and r.causal_status == 'unknown' for r in snapshot.records)
    assert all(r.provenance == 'model_hypothesis' for r in snapshot.records if r.record_type != 'hindsight_export')
    for private in (SECRET, BANK, 'SYNTHETIC_PRIVATE_MODEL', 'SYNTHETIC_PRIVATE_MEMORY', 'SYNTHETIC_PRIVATE_DIRECTIVE'):
        assert private not in repr((connector.claims, connector.summary, snapshot))


@pytest.mark.parametrize('value', [None, '{"based_on":{}}', {'based_on': None}, {'based_on': {'memories': 'malformed'}}])
def test_absent_encoded_and_malformed_categories_remain_unknown(source, value):
    archive, _, rows, _, spec, declaration = source
    rows[0]['reflect_response'] = value; (archive/'mental_models.json').write_text(json.dumps(rows))
    connector = HindsightModelReferences(declaration); snapshot = connector.read(spec)
    coverage, = connector.claims
    assert (coverage.memories, coverage.mental_models, coverage.directives) == ('unknown',)*3
    assert not connector.summary['task_outcomes_verified'] and all(r.independence == 'unknown' for r in snapshot.records)


def test_invalid_reference_id_remains_unknown(source):
    archive, _, rows, _, spec, declaration = source
    rows[0]['reflect_response']['based_on']['memories'][0]['id'] = {'raw': SECRET}
    (archive/'mental_models.json').write_text(json.dumps(rows))
    connector = HindsightModelReferences(declaration); connector.read(spec)
    assert connector.claims[0].target_identity == 'invalid' and connector.claims[0].target_sha256 is None


def test_non_object_reference_is_invalid_not_missing(source):
    archive, _, rows, _, spec, declaration = source
    rows[0]['reflect_response']['based_on']['memories'][0] = SECRET
    (archive/'mental_models.json').write_text(json.dumps(rows))
    connector = HindsightModelReferences(declaration); connector.read(spec)
    assert connector.claims[0].target_identity == 'invalid' and connector.claims[0].target_sha256 is None


@pytest.mark.parametrize('case', ['foreign_bank', 'duplicate_model', 'missing_file'])
def test_invalid_model_scope_fails_closed(source, case):
    archive, manifest, rows, _, spec, declaration = source
    if case == 'foreign_bank': rows[0]['bank_id'] = 'other'
    if case == 'duplicate_model': rows.append(dict(rows[0]))
    if case == 'missing_file': (archive/'mental_models.json').unlink()
    else: (archive/'mental_models.json').write_text(json.dumps(rows))
    with pytest.raises((ValueError, OSError)): HindsightModelReferences(declaration).read(spec)


def test_total_claim_bound_counts_coverage_and_references(source):
    archive, _, _, selection, spec, declaration = source
    selection['limits'] = {'max_rows': 4}; (declaration.parent/'selection.json').write_text(json.dumps(selection))
    spec = SourceSpec(**{**spec.to_dict(), 'scope_sha256': digest(selection)})
    declaration.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    with pytest.raises(ValueError): HindsightModelReferences(declaration).read(spec)
    assert archive.exists()


def test_wrong_bank_blocks_model_file_before_read_and_changes_are_refused(source):
    archive, manifest, _, _, spec, declaration = source
    manifest['source_bank_id'] = 'other'; (archive/'manifest.json').write_text(json.dumps(manifest))
    with patch('agent_harness_lab.sources.hindsight_knowledge.read_regular', wraps=hindsight_knowledge.read_regular) as read:
        with pytest.raises(PermissionError): HindsightModelReferences(declaration).read(spec)
    assert [call.args[0].name for call in read.call_args_list] == ['manifest.json']
    manifest['source_bank_id'] = BANK; (archive/'manifest.json').write_text(json.dumps(manifest))
    with patch('agent_harness_lab.sources.hindsight_knowledge.regular_sha256', return_value='0'*64):
        with pytest.raises(ValueError): HindsightModelReferences(declaration).read(spec)


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
    assert code == 0 and preview['hindsight_refs_summary']['capture_completeness'] == 'unknown'
    assert invoke('import')[0] == 0 and invoke('import')[1]['imported'] == 0
    assert SECRET not in cache.read_text() and BANK not in cache.read_text()
