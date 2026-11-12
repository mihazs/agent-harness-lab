"""Synthetic serialized Hindsight JSON fields; decoding remains unverified and bounded."""
import contextlib
import io
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources.hindsight_model_refs import HindsightModelReferences
from agent_harness_lab.sources.schema import SourceSpec, digest
from tests.unit.sources.test_source_hindsight_model_refs import BANK, SECRET
from tests.unit.sources import test_source_hindsight_model_refs as base

source = base.source  # reuse the v1 fixture; pytest resolves it by this module-level name


def v2(source, *, encoding='serialized', nested=None):
    archive, manifest, rows, selection, spec, declaration = source
    if encoding is not None: manifest['bank_rows_json_encoding'] = encoding
    if nested is None: nested = json.dumps(rows[0]['reflect_response'])
    rows[0]['reflect_response'] = nested
    (archive/'manifest.json').write_text(json.dumps(manifest)); (archive/'mental_models.json').write_text(json.dumps(rows))
    spec = SourceSpec(**{**spec.to_dict(), 'version': 'v2'})
    declaration.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    return archive, manifest, rows, selection, spec, declaration


def test_serialized_refs_are_distinct_private_and_unverified(source):
    _, _, _, _, spec, declaration = v2(source)
    connector = HindsightModelReferences(declaration); snapshot = connector.read(spec)
    refs = [c for c in connector.claims if c.claim_type == 'model_reference']
    coverage, = [c for c in connector.claims if c.claim_type == 'model_reference_coverage']
    assert len(refs) == 4 and refs[0].target_sha256 == refs[1].target_sha256
    assert refs[0].native_id_sha256 != refs[1].native_id_sha256
    assert (coverage.memories, coverage.mental_models, coverage.directives) == ('reported_list',)*3
    assert connector.summary['source_version'] == 'v2' and connector.summary['capture_completeness'] == 'unknown'
    assert 'row_encoding_declaration_unverified' in connector.summary['warnings']
    assert snapshot.graph is None and all(r.independence == 'unknown' for r in snapshot.records)
    assert all(private not in repr((connector.claims, connector.summary, snapshot)) for private in (SECRET, BANK, 'SYNTHETIC_PRIVATE_MEMORY'))


@pytest.mark.parametrize('encoding', [None, 'decoded', 'unexpected'])
def test_string_without_explicit_serialized_declaration_remains_unknown(source, encoding):
    _, _, _, _, spec, declaration = v2(source, encoding=encoding)
    connector = HindsightModelReferences(declaration); connector.read(spec)
    coverage, = connector.claims
    assert (coverage.memories, coverage.mental_models, coverage.directives) == ('unknown',)*3


def test_declared_serialized_but_object_value_remains_unknown(source):
    _, _, rows, _, _, _ = source
    _, _, _, _, spec, declaration = v2(source, nested=rows[0]['reflect_response'])
    connector = HindsightModelReferences(declaration); connector.read(spec)
    coverage, = connector.claims
    assert coverage.memories == 'unknown'


@pytest.mark.parametrize('nested', ['not-json', '{"based_on":', '{"x":1,"x":2}', 'null'])
def test_malformed_or_empty_nested_values_remain_unknown(source, nested):
    _, _, _, _, spec, declaration = v2(source, nested=nested)
    connector = HindsightModelReferences(declaration); connector.read(spec)
    coverage, = connector.claims
    assert (coverage.memories, coverage.mental_models, coverage.directives) == ('unknown',)*3


def test_nested_depth_limit_is_enforced(source):
    _, _, _, _, spec, declaration = v2(source, nested='['*20+'0'+']'*20)
    with pytest.raises(ValueError): HindsightModelReferences(declaration).read(spec)


def test_aggregate_nested_container_budget_is_enforced(source):
    archive, manifest, rows, selection, spec, declaration = v2(source, nested='['*10+'0'+']'*10)
    selection['limits'] = {'max_json_containers': 12}
    (declaration.parent/'selection.json').write_text(json.dumps(selection))
    spec = SourceSpec(**{**spec.to_dict(), 'scope_sha256': digest(selection)})
    declaration.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    with pytest.raises(ValueError): HindsightModelReferences(declaration).read(spec)
    assert archive.exists() and manifest and rows


def test_cli_v2_preview_import_is_offline_private_and_idempotent(source, tmp_path):
    _, _, _, _, spec, declaration = v2(source)
    cache = tmp_path/'cache.json'
    def invoke(operation):
        output = io.StringIO()
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(output):
            code = main(['sources', operation, '--manifest', str(declaration), '--project-sha256', spec.project_sha256,
                         '--scope-sha256', spec.scope_sha256, '--cache', str(cache)])
        assert SECRET not in output.getvalue() and BANK not in output.getvalue()
        return code, json.loads(output.getvalue())
    code, preview = invoke('preview')
    assert code == 0 and preview['hindsight_refs_summary']['source_version'] == 'v2'
    assert invoke('import')[0] == 0 and invoke('import')[1]['imported'] == 0
    assert SECRET not in cache.read_text() and BANK not in cache.read_text()


def test_v1_does_not_decode_even_when_manifest_declares_serialized(source):
    archive, manifest, rows, _, spec, declaration = source
    manifest['bank_rows_json_encoding'] = 'serialized'; rows[0]['reflect_response'] = json.dumps(rows[0]['reflect_response'])
    (archive/'manifest.json').write_text(json.dumps(manifest)); (archive/'mental_models.json').write_text(json.dumps(rows))
    connector = HindsightModelReferences(declaration); connector.read(spec)
    coverage, = connector.claims
    assert (coverage.memories, coverage.mental_models, coverage.directives) == ('unknown',)*3
    assert connector.summary['source_version'] == 'v1'
