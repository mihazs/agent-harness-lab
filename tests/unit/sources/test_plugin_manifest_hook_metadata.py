"""Synthetic plugin hook-key metadata never interprets or exposes its value."""
import json

import pytest

from agent_harness_lab import surfaces
from agent_harness_lab.sources import plugins_inventory


@pytest.mark.parametrize('hooks', ['SYNTHETIC_SECRET', {'PreToolUse': 'SYNTHETIC_SECRET'}, None])
def test_manifest_hooks_key_observation_is_unvalidated_and_private(tmp_path, hooks):
    manifest = tmp_path / '.claude-plugin/plugin.json'
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({'name': 'synthetic', 'hooks': hooks}))
    profile = dict(schema_version=1, runtime='claude-code', version=surfaces.PINS['claude-code'], task_cwd='.')
    old = plugins_inventory._CheckoutWalk(profile, 1)
    new = plugins_inventory._CheckoutWalk(profile, 2)
    old.root(str(tmp_path), 'caller_declared_external', 'synthetic')
    new.root(str(tmp_path), 'caller_declared_external', 'synthetic')
    old_node = next(node for node in old.nodes if node.path == '.claude-plugin/plugin.json')
    new_node = next(node for node in new.nodes if node.path == '.claude-plugin/plugin.json')
    assert 'manifest_hooks_key_unvalidated' not in dict(old_node.facts)
    assert dict(new_node.facts)['manifest_hooks_key_unvalidated'] is True
    assert 'SYNTHETIC_SECRET' not in str(new_node)


def test_manifest_without_hooks_key_does_not_imply_missing_default_hooks():
    status, facts = plugins_inventory.parse_manifest('.claude-plugin/plugin.json', b'{"name":"synthetic"}',
                                                     {'runtime': 'claude-code'})
    assert status == 'parsed' and facts['manifest_hooks_key_unvalidated'] is False
