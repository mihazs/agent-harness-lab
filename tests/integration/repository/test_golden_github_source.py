"""Offline declaration consistency does not establish remote identity or capture."""
import json
from pathlib import Path

import pytest

from agent_harness_lab.sources.github import GitHubSelection
from agent_harness_lab.sources.github_config import GitHubConfigured

GOLDEN = Path(__file__).resolve().parents[2] / 'support/golden_profiles/github-api-source-v1-input.json'


def test_saved_github_v1_declaration_checks_version_and_scope_offline(tmp_path):
    manifest = json.loads(GOLDEN.read_text())
    source = manifest['source']
    declaration, saved = GitHubConfigured(GOLDEN).declaration(
        project_sha256=source['project_sha256'], scope_sha256=source['scope_sha256'])
    assert saved == manifest
    assert declaration.to_dict() == source
    selection = GitHubSelection.parse(saved['selection'])
    assert json.loads(json.dumps(selection.to_dict())) == saved['selection']
    assert selection.source_version == source['version'] == '2026-03-10-v1'

    for field in ('version', 'scope_sha256'):
        changed = json.loads(GOLDEN.read_text())
        changed['source'][field] = '2026-03-10-v2' if field == 'version' else '0' * 64
        path = tmp_path / (field + '.json')
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match='API configuration exceeds its authorized identity/scope'):
            GitHubConfigured(path).declaration(
                project_sha256=source['project_sha256'], scope_sha256=source['scope_sha256'])
