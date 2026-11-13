"""Synthetic metadata-only checks; protected files are never opened."""
import os
from unittest.mock import patch

from agent_harness_lab.audit import protected_paths
from agent_harness_lab.sources import harness_inventory


def test_candidates_are_metadata_only_and_enforcement_unknown(tmp_path):
    files = ('.env', '.env.local', 'app/secret.key', '.github/workflows/check.yml', 'eslint.config.js', 'other.txt')
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('SYNTHETIC_SECRET')
    with patch.object(harness_inventory._Walk, 'read', side_effect=AssertionError('no file reads')):
        result = protected_paths.capture(tmp_path)
    assert result['status'] == 'completed' and result['coverage'] == 'walked_checkout_metadata_only'
    assert {row['path'] for row in result['paths']} == set(files) - {'other.txt'}
    assert all(row['content'] == 'not_read' and row['enforcement'] == 'unknown' for row in result['paths'])
    assert 'SYNTHETIC_SECRET' not in str(result) and result['publication_allowed'] is False


def test_refused_paths_and_bounds_leave_coverage_unknown(tmp_path, monkeypatch):
    (tmp_path / '.env').symlink_to('/nonexistent')
    result = protected_paths.capture(tmp_path)
    assert result['coverage'] == 'unknown' and result['paths'] == []
    monkeypatch.setattr(harness_inventory, 'MAX_ENTRIES', 0)
    assert protected_paths.capture(tmp_path) == dict(profile=protected_paths.PROFILE, status='blocked', paths=[],
                                                       coverage='unknown', publication_allowed=False)
    assert os.path.islink(tmp_path / '.env')


def test_additional_named_format_and_lint_configs_are_metadata_only(tmp_path):
    names = ('eslint.config.cjs', '.prettierrc.yaml', '.prettierrc.yml', 'prettier.config.mjs',
             'prettier.config.cjs', 'biome.json', 'biome.jsonc')
    for name in (*names, 'biome.json.backup'):
        (tmp_path / name).write_text('SYNTHETIC_SECRET')
    with patch.object(harness_inventory._Walk, 'read', side_effect=AssertionError('no file reads')):
        result = protected_paths.capture(tmp_path)
    assert {row['path'] for row in result['paths']} == set(names)
    assert all(row['category'] == 'config_protection' and row['enforcement'] == 'unknown' for row in result['paths'])
    assert 'SYNTHETIC_SECRET' not in str(result)


def test_a_git_ignored_directory_is_not_walked_while_coverage_stays_walked(tmp_path):
    # docs/static-audit.md states this omission: the coverage value does not record it, so the documentation must.
    (tmp_path / '.git').mkdir(); (tmp_path / '.gitignore').write_text('vendor/\n.env\n')
    for name in ('vendor/deep.pem', 'vendor/.env', '.env', 'keys/app.key'):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True); (tmp_path / name).write_text('')
    result = protected_paths.capture(tmp_path)
    assert {row['path'] for row in result['paths']} == {'.env', 'keys/app.key'}
    assert result['coverage'] == 'walked_checkout_metadata_only'
