"""Synthetic Hindsight model rows; synthesized claims are never outcomes."""
import contextlib
import io
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources import hindsight_knowledge
from agent_harness_lab.sources.hindsight_models import HindsightModels
from agent_harness_lab.sources.schema import SourceSpec, digest

INSTANCE, BANK = 'synthetic-instance', 'SYNTHETIC_PRIVATE_BANK'
SECRET = 'SYNTHETIC_PRIVATE_CONTENT'


@pytest.fixture
def source(tmp_path):
    archive = tmp_path/'archive'; archive.mkdir()
    manifest = dict(schema_version=1, source_bank_id=BANK, document_count=0, observation_count=0,
        invalidated_memory_count=0, mental_model_count=1, knowledge_page_count=1)
    rows = [dict(id='SYNTHETIC_PRIVATE_MODEL', bank_id=BANK, name='SYNTHETIC_PRIVATE_NAME',
        source_query='SYNTHETIC_PRIVATE_QUERY', content=SECRET, last_refreshed_at='2031-04-01T00:00:00Z'),
        dict(id='SYNTHETIC_PRIVATE_BACKING', bank_id=BANK, name='SYNTHETIC_PRIVATE_PAGE', content=SECRET)]
    (archive/'manifest.json').write_text(json.dumps(manifest)); (archive/'mental_models.json').write_text(json.dumps(rows))
    selection = dict(schema_version=1, hindsight_version='1.1.0', instance=INSTANCE, bank_id=BANK, archive='archive')
    spec = SourceSpec('local_file', 'hindsight-mental-models', 'v1', digest(str(tmp_path.resolve())),
        digest(('hindsight.bank', INSTANCE, BANK)), digest(selection), identity_provenance='source_declared_id', enabled=True)
    (tmp_path/'selection.json').write_text(json.dumps(selection))
    declaration = tmp_path/'source.json'
    declaration.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    return archive, manifest, rows, selection, spec, declaration


def test_model_rows_are_private_syntheses_not_independent_outcomes(source):
    _, _, _, _, spec, declaration = source
    connector = HindsightModels(declaration); snapshot = connector.read(spec)
    assert len(connector.claims) == 2 and connector.summary['returned'] == 2
    assert connector.claims[0].refreshed_at == '2031-04-01T00:00:00+00:00'
    assert connector.summary['capture_completeness'] == 'unknown' and not connector.summary['task_outcomes_verified']
    models = [r for r in snapshot.records if r.record_type == 'hindsight_mental_model']
    assert all((r.provenance, r.causal_status, r.independence) == ('model_hypothesis', 'unknown', 'unknown') for r in models)
    for private in (SECRET, BANK, 'SYNTHETIC_PRIVATE_MODEL', 'SYNTHETIC_PRIVATE_QUERY', 'SYNTHETIC_PRIVATE_NAME'):
        assert private not in repr((connector.claims, connector.summary, snapshot))


def test_wrong_bank_is_refused_before_model_file_open(source):
    archive, manifest, _, _, spec, declaration = source
    manifest['source_bank_id'] = 'other'; (archive/'manifest.json').write_text(json.dumps(manifest))
    with patch('agent_harness_lab.sources.hindsight_knowledge.read_regular', wraps=hindsight_knowledge.read_regular) as read:
        with pytest.raises(PermissionError): HindsightModels(declaration).read(spec)
    assert [call.args[0].name for call in read.call_args_list] == ['manifest.json']


@pytest.mark.parametrize('case', ['foreign_bank', 'duplicate', 'bad_id', 'bad_body', 'count_short'])
def test_bad_model_rows_fail_closed(source, case):
    archive, manifest, rows, _, spec, declaration = source
    if case == 'foreign_bank': rows[0]['bank_id'] = 'other'
    if case == 'duplicate': rows[1]['id'] = rows[0]['id']
    if case == 'bad_id': rows[0]['id'] = True
    if case == 'bad_body': rows[0]['content'] = dict(raw=SECRET)
    if case == 'count_short': manifest['mental_model_count'] = 3
    (archive/'manifest.json').write_text(json.dumps(manifest)); (archive/'mental_models.json').write_text(json.dumps(rows))
    with pytest.raises(ValueError): HindsightModels(declaration).read(spec)


def test_invalid_and_placeholder_refresh_times_remain_unknown(source):
    archive, _, rows, _, spec, declaration = source
    rows[0]['last_refreshed_at'] = '2031-04-01T00:00:00'
    rows[1]['last_refreshed_at'] = '9999-12-31T23:59:59Z'
    (archive/'mental_models.json').write_text(json.dumps(rows))
    connector = HindsightModels(declaration); connector.read(spec)
    assert [(c.refreshed_at, c.refreshed_at_status) for c in connector.claims] == [(None, 'invalid'), (None, 'placeholder')]


def test_file_mutation_and_symlink_are_refused(source, tmp_path):
    archive, _, _, _, spec, declaration = source
    with patch('agent_harness_lab.sources.hindsight_knowledge.regular_sha256', return_value='0'*64):
        with pytest.raises(ValueError): HindsightModels(declaration).read(spec)
    target = archive/'mental_models.json'; target.unlink(); target.symlink_to(tmp_path/'other')
    with pytest.raises((ValueError, OSError)): HindsightModels(declaration).read(spec)


def test_missing_model_file_is_refused_even_at_zero_declared_count(source):
    archive, manifest, _, _, spec, declaration = source
    manifest['mental_model_count'] = 0
    (archive/'manifest.json').write_text(json.dumps(manifest)); (archive/'mental_models.json').unlink()
    with pytest.raises((ValueError, OSError)): HindsightModels(declaration).read(spec)


def test_cli_preview_import_offline_private_and_idempotent(source, tmp_path):
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
    assert code == 0 and preview['hindsight_models_summary']['capture_completeness'] == 'unknown'
    assert invoke('import')[0] == 0 and invoke('import')[1]['imported'] == 0
    assert SECRET not in cache.read_text() and BANK not in cache.read_text()
