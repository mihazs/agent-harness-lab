"""Synthetic configuration facts are declared, not evidence of runtime loading."""
import json
from pathlib import Path

import pytest

from agent_harness_lab import config_recommendations
from agent_harness_lab.cli import main
from agent_harness_lab.sources.config_inventory import capture
from tests.unit.evaluation.test_benchmark_contracts import record

GOLDENS = Path(__file__).resolve().parents[2] / 'support/golden_profiles'
CONFIG = '''developer_instructions = "synthetic instructions"
[features]
memories = true
[mcp_servers.synthetic]
url = "https://example.invalid/mcp"
enabled = false
'''


def test_saved_nonempty_config_inventory_is_consumed_without_loading_claim(tmp_path):
    config = tmp_path / '.codex/config.toml'
    config.parent.mkdir()
    config.write_text(CONFIG)
    profile = dict(schema_version=1, runtime='codex', version='0.166.0', task_cwd='.')
    inventory = json.loads(json.dumps(capture(tmp_path, profile)))
    assert inventory == json.loads((GOLDENS / 'ahl-config-inventory-v1-nonempty.json').read_text())
    assert {row['surface'] for row in inventory['rows']} == {'instructions', 'mcp', 'settings'}
    assert inventory['effective'] == 'unknown' and not inventory['loading_evidence']
    assert 'synthetic instructions' not in json.dumps(inventory)
    assert 'example.invalid' not in json.dumps(inventory)
    recommendations = json.loads(json.dumps(config_recommendations.recommend(inventory)))
    assert recommendations == json.loads((GOLDENS / 'ahl-config-recommendations-v1-nonempty.json').read_text())
    assert recommendations['rows'] and not recommendations['publication_allowed']
    with pytest.raises(ValueError, match='expected a ahl-config-inventory-v1 document'):
        config_recommendations.recommend(inventory | {'profile': 'unknown-v999'})
    for index, value in enumerate((inventory, recommendations)):
        forged = tmp_path / f'forged-{index}.json'
        forged.write_text(json.dumps([value | record() | {'publication_allowed': True}]))
        public = tmp_path / f'public-{index}'
        with pytest.raises(SystemExit) as exc:
            main(['benchmark-report', '--input', str(forged), '--output', str(public), '--public'])
        assert exc.value.code == 2
        assert not public.exists()
