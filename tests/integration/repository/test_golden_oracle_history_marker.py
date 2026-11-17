"""A synthetic private-store format marker does not assert history or authority."""
import json
from pathlib import Path

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.evaluation.grader_correction import CorrectionStore
from agent_harness_lab.private_journal import PrivateJournal
from tests.unit.evaluation.test_benchmark_contracts import record

GOLDENS = Path(__file__).resolve().parents[2] / 'support/golden_profiles'


@pytest.mark.parametrize(('store_type', 'profile'), [
    (HistoryStore, 'experiment-oracle-history-v1'), (CorrectionStore, 'grader-correction-review-v1'),
    (lambda root: PrivateJournal(root, 'history-search-inputs-v1', lambda *_: False), 'history-search-inputs-v1')])
def test_saved_history_marker_opens_only_the_expected_store_format(tmp_path, store_type, profile):
    golden = GOLDENS / (profile + '-marker.json')
    root = tmp_path / 'history'
    root.mkdir(mode=0o700)
    marker = root / '.store.json'
    marker.write_bytes(golden.read_bytes())
    marker.chmod(0o600)
    store = store_type(root)
    with store.locked():
        assert not (root / 'cases').exists()
    wrong = json.loads(golden.read_text()) | {'format': 'unsupported-store-v0'}
    marker.write_text(json.dumps(wrong))
    with pytest.raises(ValueError, match='(?i)(store|marker)'):
        with store.locked():
            pass
    forged = tmp_path / 'forged.json'
    forged.write_text(json.dumps([json.loads(golden.read_text()) | record() | {'publication_allowed': True}]))
    public = tmp_path / 'public'
    with pytest.raises(SystemExit) as exc:
        main(['benchmark-report', '--input', str(forged), '--output', str(public), '--public'])
    assert exc.value.code == 2
    assert not public.exists()
