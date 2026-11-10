"""Synthetic bounded lexical references, not enforcement claims."""
import json

import pytest

from agent_harness_lab import surfaces
from agent_harness_lab.audit import constraint_references, protected_paths
from agent_harness_lab.sources import harness_inventory


def setup_project(tmp_path):
    source = tmp_path / 'CLAUDE.md'
    raw = b'Never edit `.env`.\nDo not weaken eslint.config.js.\nNo constraint here.\n'
    source.write_bytes(raw)
    settings = tmp_path / '.claude/settings.json'
    settings.parent.mkdir()
    settings.write_text(json.dumps({'permissions': {'deny': ['Edit(.env)']}}))
    (tmp_path / '.env').write_text('SYNTHETIC_SECRET')
    (tmp_path / 'eslint.config.js').write_text('synthetic')
    profile = dict(schema_version=1, runtime='claude-code', version=surfaces.PINS['claude-code'], task_cwd='.')
    inventory = harness_inventory.capture(tmp_path, profile)
    protected = protected_paths.capture(tmp_path)
    return inventory, protected, raw


def test_private_line_references_keep_candidate_and_enforcement_distinct(tmp_path):
    inventory, permissions, raw = setup_project(tmp_path)
    result = constraint_references.report(inventory, 'CLAUDE.md', raw, permissions)
    assert [row['line'] for row in result['constraints']] == [1, 2]
    assert result['constraints'][0]['protected_paths'] == [dict(path='.env', permission='permission_candidate_present')]
    assert result['constraints'][1]['protected_paths'] == [dict(path='eslint.config.js', permission='unknown')]
    assert all(row['hook_match'] == row['enforcement'] == 'unknown' for row in result['constraints'])
    assert result['publication_allowed'] is False and result['observed_loading'] == 'unknown'
    assert 'Never edit' not in str(result) and 'SYNTHETIC_SECRET' not in str(result)


def test_changed_source_and_oversized_constraint_fail_closed(tmp_path):
    inventory, permissions, raw = setup_project(tmp_path)
    with pytest.raises(ValueError, match='do not match'):
        constraint_references.report(inventory, 'CLAUDE.md', raw + b'changed', permissions)
    long = b'Never ' + b'x' * constraint_references.MAX_LINE
    (tmp_path / 'CLAUDE.md').write_bytes(long)
    profile = dict(schema_version=1, runtime='claude-code', version=surfaces.PINS['claude-code'], task_cwd='.')
    with pytest.raises(ValueError, match='bound exceeded'):
        constraint_references.report(harness_inventory.capture(tmp_path, profile), 'CLAUDE.md', long, permissions)


def test_longer_path_does_not_match_prefix(tmp_path):
    _, protected, _ = setup_project(tmp_path)
    raw = b'Never edit .env.local.\n'
    (tmp_path / 'CLAUDE.md').write_bytes(raw)
    profile = dict(schema_version=1, runtime='claude-code', version=surfaces.PINS['claude-code'], task_cwd='.')
    result = constraint_references.report(harness_inventory.capture(tmp_path, profile), 'CLAUDE.md', raw, protected)
    assert result['constraints'][0]['protected_paths'] == []
