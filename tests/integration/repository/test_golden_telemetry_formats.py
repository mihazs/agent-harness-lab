"""Synthetic telemetry formats carry execution observations, not task outcomes."""
import json
from pathlib import Path

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.telemetry.adapters import normalize
from agent_harness_lab.telemetry.schema import verified_outcome
from tests.unit.evaluation.test_benchmark_contracts import record

GOLDENS = Path(__file__).resolve().parents[2] / 'support/golden_profiles'


@pytest.mark.parametrize('kind', ['claude', 'codex'])
def test_saved_synthetic_telemetry_format_keeps_outcome_unknown(tmp_path, kind):
    version = kind + '-jsonl-v1'
    source = json.loads((GOLDENS / (version + '-input.json')).read_text())
    output = list(normalize(source, kind, source_version=version))
    assert output == json.loads((GOLDENS / (version + '-output.json')).read_text())
    assert [item['activity'] for item in output] == ['tool_call', 'tool_result']
    assert output[1]['execution_status'] == 'error'
    assert all(verified_outcome(item) is None for item in output)
    assert 'synthetic body' not in json.dumps(output)
    with pytest.raises(ValueError, match='unsupported native capture version'):
        list(normalize(source, kind, source_version=kind + '-jsonl-v0'))
    forged = tmp_path / 'forged.json'
    forged.write_text(json.dumps([output[0] | record() | {'publication_allowed': True}]))
    public = tmp_path / 'public'
    with pytest.raises(SystemExit) as exc:
        main(['benchmark-report', '--input', str(forged), '--output', str(public), '--public'])
    assert exc.value.code == 2
    assert not public.exists()
