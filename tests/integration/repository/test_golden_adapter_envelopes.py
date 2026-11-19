"""Synthetic presentation contracts do not grant execution or retry authority."""
import json
from pathlib import Path

import pytest

from agent_harness_lab.cli import main
from tests.unit.evaluation.test_benchmark_contracts import record
from agent_harness_lab.evaluation.adapters import AdapterSpec, Authorization, normalize_error, verify

GOLDENS = Path(__file__).resolve().parents[2] / 'support/golden_profiles'


def test_saved_adapter_contract_and_envelopes_preserve_error_authority(tmp_path):
    spec_data = json.loads((GOLDENS / 'adapter-contract-spec-v1.json').read_text())
    spec = AdapterSpec.from_mapping(spec_data)
    assert spec.as_mapping() == spec_data
    assert (spec.input_contract, spec.output_contract) == ('tool-envelope-v1', 'presented-envelope-v1')
    assert 'structured-error-v1' in spec.transformations
    original = json.loads((GOLDENS / 'tool-envelope-v1.json').read_text())
    expected = json.loads((GOLDENS / 'presented-envelope-v1.json').read_text())
    authorization = Authorization('synthetic-project', ('read',), ('structured-error-v1',), 'v1')
    assert normalize_error(original, authorization) == expected
    assert verify(original, expected, authorization)
    assert expected['error_metadata']['retry_authorized'] is False
    assert not verify(original, expected | {'error_metadata': {'retry_authorized': True}}, authorization)
    with pytest.raises(ValueError, match='adapter/tool not explicitly authorized'):
        normalize_error(original | {'tool': 'other'}, authorization)
    for index, value in enumerate((spec_data, original, expected)):
        forged = tmp_path / f'forged-{index}.json'
        forged.write_text(json.dumps([value | record() | {'publication_allowed': True}]))
        public = tmp_path / f'public-{index}'
        with pytest.raises(SystemExit) as exc:
            main(['benchmark-report', '--input', str(forged), '--output', str(public), '--public'])
        assert exc.value.code == 2
        assert not public.exists()


@pytest.mark.parametrize('field', ['input_contract', 'output_contract'])
def test_unknown_adapter_contract_version_is_rejected(field):
    saved = json.loads((GOLDENS / 'adapter-contract-spec-v1.json').read_text())
    saved[field] = saved[field].replace('-v1', '-v2')
    with pytest.raises(ValueError, match='unsupported input/output contract'):
        AdapterSpec.from_mapping(saved)
