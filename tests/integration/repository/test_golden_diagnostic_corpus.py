"""Read one frozen synthetic corpus export without claiming review authority."""
import hashlib
import json
from pathlib import Path

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.diagnostics.corpus import development_export
from agent_harness_lab.private_journal import encoded
from tests.unit.evaluation.test_benchmark_contracts import record

GOLDEN = Path(__file__).resolve().parents[2]/'support/golden_profiles/diagnostic-corpus-v1.json'


def test_saved_corpus_export_requires_named_migration_and_stays_private(tmp_path):
    value = json.loads(GOLDEN.read_text())
    assert value['records'][0]['gold_label'] is False and value['publication_allowed'] is False
    with pytest.raises(ValueError, match='diagnostic_corpus_v1_requires_migration'):
        development_export(value)
    with pytest.raises(ValueError, match='diagnostic corpus manifest digest differs'):
        development_export(value | {'revision_sha256': 'a' * 64})
    wrong = {**value, 'profile': 'unknown-v999'}
    wrong['manifest_sha256'] = hashlib.sha256(encoded({k:v for k,v in wrong.items() if k != 'manifest_sha256'})).hexdigest()
    with pytest.raises(ValueError, match='diagnostic corpus fields differ from frozen schema'):
        development_export(wrong)
    source = tmp_path/'input.json'; source.write_text(json.dumps([value | record() | {'publication_allowed': True}]))
    destination = tmp_path/'public'
    with pytest.raises(SystemExit) as exc:
        main(['benchmark-report', '--input', str(source), '--output', str(destination), '--public'])
    assert exc.value.code == 2 and not destination.exists()
