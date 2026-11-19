"""A lineage store marker identifies a scope; it grants no lineage authority."""
import json
from pathlib import Path

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources.lineage import LineageStore
from tests.unit.evaluation.test_benchmark_contracts import record

GOLDEN = Path(__file__).resolve().parents[2] / 'support/golden_profiles/lineage-v1-marker.json'


def test_saved_lineage_marker_requires_the_same_scope(tmp_path):
    root = tmp_path / 'store'
    root.mkdir(mode=0o700)
    saved = GOLDEN.read_bytes()
    marker = root / '.store.json'
    marker.write_bytes(saved)
    marker.chmod(0o600)
    store = LineageStore(root, project_sha256='a' * 64, scope_sha256='b' * 64)
    with store.locked():
        assert marker.read_bytes() == saved
    other = LineageStore(root, project_sha256='a' * 64, scope_sha256='c' * 64)
    with pytest.raises(ValueError, match='unsupported controller case store'):
        with other.locked():
            pass
    forged = tmp_path / 'forged.json'
    forged.write_text(json.dumps([json.loads(saved) | record() | {'publication_allowed': True}]))
    public = tmp_path / 'public'
    with pytest.raises(SystemExit) as exc:
        main(['benchmark-report', '--input', str(forged), '--output', str(public), '--public'])
    assert exc.value.code == 2
    assert not public.exists()
