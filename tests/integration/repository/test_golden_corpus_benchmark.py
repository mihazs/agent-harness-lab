"""An older synthetic corpus benchmark input requires an explicit migration."""
from pathlib import Path
import subprocess
import sys


GOLDENS = Path(__file__).resolve().parents[2] / 'support/golden_profiles'


def test_saved_corpus_benchmark_names_required_migration():
    source = GOLDENS / 'diagnostic-corpus-benchmark-v1-input.json'
    completed = subprocess.run([sys.executable, '-m', 'tools.benchmark_models', str(source),
                                '--profile', 'diagnostic-corpus', '--method', 'baseline'],
                               capture_output=True, text=True, check=False)
    assert completed.returncode != 0
    assert 'diagnostic_corpus_v1_requires_migration' in completed.stderr
    assert not completed.stdout
