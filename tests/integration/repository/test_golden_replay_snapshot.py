"""A saved replay declaration is not evidence that any replay occurred."""
import hashlib
import json
from pathlib import Path

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.regression_replay import read_spec
from tests.unit.evaluation.test_benchmark_contracts import record

GOLDEN = Path(__file__).resolve().parents[2] / 'support/golden_profiles/ahl-tree-v1-replay-input.json'


def test_saved_replay_snapshot_format_is_loaded_and_unsupported_version_rejected(tmp_path):
    raw = GOLDEN.read_bytes()
    saved = json.loads(raw)
    result, identity = read_spec(GOLDEN)
    assert result == saved
    assert identity == hashlib.sha256(raw).hexdigest()
    assert result['snapshot_format'] == 'ahl-tree-v1'
    assert result['before'] != result['after'] and result['earlier_valid'] is None

    changed = tmp_path / 'unsupported.json'
    changed.write_text(json.dumps(saved | {'snapshot_format': 'ahl-tree-v2'}))
    with pytest.raises(ValueError, match='unsupported replay schema/snapshot format'):
        read_spec(changed)


def test_replay_declaration_cannot_be_exported_as_public_benchmark(tmp_path):
    saved = json.loads(GOLDEN.read_text())
    forged = tmp_path / 'forged.json'
    forged.write_text(json.dumps([saved | record() | {'publication_allowed': True}]))
    public = tmp_path / 'public'
    with pytest.raises(SystemExit) as exc:
        main(['benchmark-report', '--input', str(forged), '--output', str(public), '--public'])
    assert exc.value.code == 2
    assert not public.exists()
