"""Offline staleness reporting (#685): launcher and skill-link releases must agree and meet the skill's minimum CLI."""
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab import installation, updater
from agent_harness_lab.onboarding import doctor

ROOT = Path(__file__).resolve().parents[3]


def release(root, version, minimum='0.1.0'):
    folder = root / 'data/releases' / version
    folder.mkdir(parents=True)
    (folder / '.release-sha256').write_text('0' * 64)
    (folder / 'ahl').write_text('#!/bin/sh\n')
    for name in installation.SKILLS:
        (folder / 'skills' / name).mkdir(parents=True)
        (folder / 'skills' / name / 'SKILL.md').write_text(f'Requires `ahl` {minimum} or later.\n')
    return folder


@pytest.fixture
def layout(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    (tmp_path / 'bin').mkdir()
    monkeypatch.setenv('PATH', str(tmp_path / 'bin'))
    monkeypatch.setenv('HOME', str(home))

    def link(launcher, skills, minimum='0.1.0'):
        """Launcher at release `launcher`, global skill links at release `skills`."""
        for version in {launcher, skills}:
            release(tmp_path, version, minimum)
        (tmp_path / 'bin/ahl').unlink(missing_ok=True)
        (tmp_path / 'bin/ahl').symlink_to(tmp_path / 'data/releases' / launcher / 'ahl')
        for folder in ('.agents/skills', '.claude/skills'):
            (home / folder).mkdir(parents=True, exist_ok=True)
            for name in installation.SKILLS:
                (home / folder / name).unlink(missing_ok=True)
                (home / folder / name).symlink_to(tmp_path / 'data/releases' / skills / 'skills' / name, target_is_directory=True)
        return installation.inspect(launcher, tmp_path / 'data/releases' / launcher, home)
    return link


def test_matching_versions_give_no_warning(layout):
    state = layout('0.2.0', '0.2.0')
    assert state['warnings'] == [] and state['launcher_release'] == '0.2.0' and len(state['skill_links']) == 4
    assert {link['release'] for link in state['skill_links']} == {'0.2.0'}


def test_skill_links_at_another_release_warn_with_the_exact_fix(layout):
    state = layout('0.2.0', '0.1.0')
    assert {w['code'] for w in state['warnings']} == {'launcher_skill_version_mismatch'} and len(state['warnings']) == 4
    assert {w['fix'] for w in state['warnings']} == {'ahl update'}
    assert all('0.2.0' in w['message'] and '0.1.0' in w['message'] for w in state['warnings'])


def test_launcher_older_than_the_skill_minimum_warns(layout):
    state = layout('0.2.0', '0.2.0', minimum='0.10.0')  # a numeric, not lexical, comparison: 0.2.0 < 0.10.0
    assert {w['code'] for w in state['warnings']} == {'cli_below_skill_minimum'}
    assert all(w['fix'] == 'ahl update' for w in state['warnings'])


def test_doctor_stays_offline_and_shows_the_warning_without_failing_readiness(layout, tmp_path):
    layout('0.2.0', '0.1.0')
    project = tmp_path / 'project'
    project.mkdir()
    with patch('urllib.request.urlopen', side_effect=AssertionError('doctor must not use the network')), \
            patch('agent_harness_lab.cli.assets', return_value=tmp_path / 'data/releases/0.2.0'), \
            patch('agent_harness_lab.cli.version', return_value='0.2.0'):
        report = doctor(project)
    check = next(c for c in report['checks'] if c['check_id'] == 'installation')
    assert (check['status'], check['required_for_profile']) == ('warn', False)
    assert '`ahl update`' in check['remediation']
    assert next(c['status'] for c in report['checks'] if c['check_id'] == 'authentication') == 'unknown'
    assert report['installation']['network_used'] is False


def test_source_checkout_is_not_a_release_installation_and_never_warns(tmp_path):
    state = installation.inspect('0.2.0', ROOT, tmp_path / 'empty-home')
    assert (state['release_installation'], state['warnings']) == (False, [])


def check(latest, capsys, installed='0.2.0'):
    def response(request, timeout):
        assert request.get_method() == 'GET' and request.full_url.endswith('/releases/latest')
        return io.BytesIO(json.dumps({'tag_name': 'v' + latest}).encode())
    with patch('urllib.request.urlopen', side_effect=response) as opened, \
            patch('agent_harness_lab.cli.version', return_value=installed):
        code = updater.main(['--check'])
    assert opened.call_count == 1
    return code, capsys.readouterr().out


def test_update_check_reports_a_newer_release_and_changes_nothing(capsys):
    code, out = check('0.10.0', capsys)
    assert code == 0 and 'update available, run: ahl update' in out and 'latest published 0.10.0' in out


def test_update_check_reports_current_when_nothing_newer(capsys):
    assert 'up to date' in check('0.2.0', capsys)[1] and 'up to date' in check('0.1.0', capsys)[1]


def test_update_check_failure_is_an_error_not_a_verdict():
    with patch('urllib.request.urlopen', side_effect=OSError('offline')), pytest.raises(SystemExit) as stopped:
        updater.main(['--check'])
    assert stopped.value.code == 2


def test_skills_declare_a_minimum_cli_no_newer_than_this_version():
    from agent_harness_lab.cli import version
    for name in installation.SKILLS:
        found = installation.MINIMUM.search((ROOT / 'skills' / name / 'SKILL.md').read_text())
        assert found and installation.key(found.group(1)) <= installation.key(version()), name
