"""Opt-in check against a caller-selected real Git checkout."""
import json
import os

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources.forge_remote import _probe, detect


def test_real_checkout_forge(capsys):
    project = os.environ.get('AHL_REAL_FORGE_PROJECT')
    expected = os.environ.get('AHL_REAL_FORGE_PROVIDER')
    if not project or not expected:
        pytest.skip('requires an explicitly selected real checkout and provider')
    assert expected in ('github', 'gitlab', 'gitea')
    assert detect(project)['provider'] == expected
    assert main(['sources', '--project', project]) == 0
    assert json.loads(capsys.readouterr().out)['provider'] == expected


def test_real_gitlab_capability_probe():
    if os.environ.get('AHL_REAL_FORGE_PROVIDER') != 'gitlab' or not os.environ.get('AHL_REAL_FORGE_PROJECT'):
        pytest.skip('requires an explicitly selected real GitLab checkout')
    assert _probe('https://gitlab.com')['provider'] == 'gitlab'


def test_real_checkout_routes_scoped_manifest(capsys):
    project = os.environ.get('AHL_REAL_FORGE_PROJECT')
    manifest = os.environ.get('AHL_REAL_FORGE_MANIFEST')
    if not project or not manifest:
        pytest.skip('requires an explicitly selected real checkout and source manifest')
    with open(manifest, encoding='utf-8') as stream:
        source = json.load(stream)['source']
    original = os.getcwd()
    try:
        os.chdir(project)
        result = main(['sources', 'probe', '--manifest', manifest, '--project-sha256', source['project_sha256'],
                       '--scope-sha256', source['scope_sha256']])
    finally:
        os.chdir(original)
    assert result == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt['forge_detection']['provider'] == source['provider']
    assert receipt['network_used'] == receipt['forge_detection']['network_used']


def test_real_checkout_rejects_missing_remote(capsys):
    project = os.environ.get('AHL_REAL_FORGE_PROJECT')
    if not project:
        pytest.skip('requires an explicitly selected real checkout')
    assert main(['sources', 'detect', project, '--remote', 'ahl-missing-remote']) == 2
    captured = capsys.readouterr()
    assert not captured.out and 'Git remote unavailable' in captured.err
    assert project not in captured.err
