"""Synthetic lexical candidates never assert effective protection."""
import json
from unittest.mock import patch

from agent_harness_lab import surfaces
from agent_harness_lab.audit import permission_candidates, protected_paths
from agent_harness_lab.sources import harness_inventory


def test_exact_project_deny_is_a_private_candidate_only(tmp_path):
    settings = tmp_path / '.claude/settings.json'
    settings.parent.mkdir()
    settings.write_text(json.dumps({'permissions': {'deny': ['Edit(.env)', 'Bash(SYNTHETIC_PRIVATE_COMMAND)']}}))
    (tmp_path / '.env').write_text('SYNTHETIC_SECRET')
    (tmp_path / 'eslint.config.js').write_text('synthetic')
    profile = dict(schema_version=1, runtime='claude-code', version=surfaces.PINS['claude-code'], task_cwd='.')
    with patch.object(protected_paths._MetadataWalk, 'read', side_effect=AssertionError('no protected contents')):
        inventory = harness_inventory.capture(tmp_path, profile, schema_version=2)
        protected = protected_paths.capture(tmp_path)
    result = permission_candidates.report(inventory, protected)
    rows = {row['path']: row for row in result['paths']}
    assert rows['.env']['status'] == 'permission_candidate_present'
    assert rows['eslint.config.js']['status'] == 'unknown'
    assert all(row['enforcement'] == 'unknown' for row in rows.values())
    assert result['coverage'] == result['effective'] == 'unknown' and result['publication_allowed'] is False
    assert 'SYNTHETIC_SECRET' not in str(result) and 'SYNTHETIC_PRIVATE_COMMAND' not in str(inventory.document())


def test_unsupported_or_malformed_denies_never_imply_no_guard(tmp_path):
    settings = tmp_path / '.claude/settings.json'
    settings.parent.mkdir()
    (tmp_path / '.env').write_text('synthetic')
    profile = dict(schema_version=1, runtime='claude-code', version=surfaces.PINS['claude-code'], task_cwd='.')
    for deny in (['Edit(**/.env)'], [42], 'Edit(.env)'):
        settings.write_text(json.dumps({'permissions': {'deny': deny}}))
        result = permission_candidates.report(harness_inventory.capture(tmp_path, profile, schema_version=2), protected_paths.capture(tmp_path))
        assert result['paths'][0]['status'] == 'unknown'
        assert result['coverage'] == 'unknown'


def test_excluded_local_settings_do_not_become_evidence(tmp_path):
    settings = tmp_path / '.claude/settings.local.json'
    settings.parent.mkdir()
    settings.write_text(json.dumps({'permissions': {'deny': ['Edit(.env)']}}))
    (tmp_path / '.env').write_text('synthetic')
    profile = dict(schema_version=1, runtime='claude-code', version=surfaces.PINS['claude-code'], task_cwd='.')
    result = permission_candidates.report(harness_inventory.capture(tmp_path, profile, schema_version=2), protected_paths.capture(tmp_path))
    assert result['paths'][0]['status'] == 'unknown'
    assert result['effective'] == 'unknown'


def test_inventory_versions_preserve_v1_and_bind_v2_evidence(tmp_path):
    settings = tmp_path / '.claude/settings.json'
    settings.parent.mkdir()
    settings.write_text(json.dumps({'permissions': {'deny': ['Edit(.env)']}}))
    (tmp_path / '.env').write_text('synthetic')
    profile = dict(schema_version=1, runtime='claude-code', version=surfaces.PINS['claude-code'], task_cwd='.')
    old = harness_inventory.capture(tmp_path, profile)
    new = harness_inventory.capture(tmp_path, profile, schema_version=2)
    assert old.document()['profile'] == 'ahl-harness-inventory-v1'
    assert new.document()['profile'] == 'ahl-harness-inventory-v2' and old.sha256 != new.sha256
    protected = protected_paths.capture(tmp_path)
    assert permission_candidates.report(old, protected)['paths'][0]['status'] == 'unknown'
    assert permission_candidates.report(new, protected)['paths'][0]['status'] == 'permission_candidate_present'
    settings.write_text(json.dumps({'permissions': {'deny': ['Write(.env)']}}))
    assert harness_inventory.capture(tmp_path, profile, schema_version=2).sha256 != new.sha256
