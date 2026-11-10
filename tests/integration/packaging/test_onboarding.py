import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import shutil
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab import config
from agent_harness_lab.onboarding import doctor


def invoke(*args):
    output, error = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
        try: code = main(list(args))
        except SystemExit as exc: code = exc.code
    return code, output.getvalue(), error.getvalue()


def test_offline_doctor_does_not_execute_connect_or_read_auth(tmp_path):
    (tmp_path/'.auth').write_text('SECRET_CANARY')
    (tmp_path/'AGENTS.md').write_text('SECRET_CANARY')
    with patch.dict(os.environ, TOKEN='SECRET_CANARY', HOME=str(tmp_path)), \
            patch('subprocess.Popen', side_effect=AssertionError('no processes')), \
            patch.object(socket, 'socket', side_effect=AssertionError('no network')):
        code, output, error = invoke('doctor', str(tmp_path), '--profile', 'offline', '--json')
    assert code == 0, error
    assert 'SECRET_CANARY' not in output + error
    report = json.loads(output)
    assert report['status'] == 'ready'
    assert not report['external_runtime_executed']
    assert sorted(p.name for p in tmp_path.iterdir()) == ['.auth', 'AGENTS.md']
    assert next(c['status'] for c in report['checks'] if c['check_id'] == 'authentication') == 'unknown'


def test_missing_runtime_and_isolation_block_only_required_profiles(tmp_path):
    assert doctor(tmp_path, 'offline')['status'] == 'ready'
    assert doctor(tmp_path, 'cooperative')['status'] == 'blocked'
    report = doctor(tmp_path, 'isolated')
    assert report['status'] == 'blocked'
    assert next(c['status'] for c in report['checks'] if c['check_id'] == 'isolated_execution') == 'blocked'
    assert not report['automatic_promotion']


def test_preview_then_exclusive_write_preserves_sentinel_and_permissions(tmp_path):
    args = ('init', str(tmp_path), '--non-interactive', '--json')
    code, preview, error = invoke(*args)
    assert code == 0, error
    assert json.loads(preview)['status'] == 'preview'
    assert list(tmp_path.iterdir()) == []
    code, output, error = invoke(*args, '--write')
    assert code == 0, error
    path = tmp_path/'ahl.json'; original = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    assert invoke(*args, '--write')[0] == 2
    assert path.read_bytes() == original
    assert doctor(tmp_path)['status'] == 'ready'


def test_preview_doctor_and_improve_share_limits_without_writing(tmp_path):
    from argparse import Namespace
    from agent_harness_lab.one_call import resolve
    code, preview, error = invoke('init', str(tmp_path), '--non-interactive', '--json')
    assert code == 0, error
    template = json.loads(preview)['configuration']
    assert doctor(tmp_path)['status'] == 'ready'
    args = Namespace(project=tmp_path, timeout=None, agent='auto', codex='codex', claude='claude')
    resolve(args, set())
    assert (args.timeout, args.trial_limit) == (template['limits']['seconds'], template['limits']['trials'])
    assert args.agent == 'auto'
    assert not (tmp_path/'ahl.json').exists()


def test_shared_resolver_validates_persisted_and_explicit_invocation_overrides(tmp_path):
    settings = config.prepare(dict(profile='cooperative', runtime=dict(name='codex', executable='/synthetic/codex'),
                                   limits=dict(seconds=90, trials=8)))
    config.create(tmp_path, settings)
    resolved = config.resolve(tmp_path, dict(agent='claude', codex='codex', claude='claude', timeout=12),
                              {'--agent', '--timeout'})
    assert resolved['source'] == 'project'
    assert (resolved['options']['agent'], resolved['options']['timeout'], resolved['trial_limit']) == ('claude', 12, 8)
    assert resolved['configuration_sha256'] == config.load(tmp_path/'ahl.json')[1]
    assert doctor(tmp_path)['profile'] == 'cooperative'


def test_interactive_cancel_and_non_tty_never_write(tmp_path):
    with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='q'):
        assert invoke('init', str(tmp_path), '--write')[0] == 1
    with patch('sys.stdin.isatty', return_value=False), patch('builtins.input', side_effect=AssertionError('must not wait')):
        assert invoke('init', str(tmp_path), '--write')[0] == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('changes', [
    pytest.param(dict(token='SECRET_CANARY'), id='unknown-key'),
    pytest.param(dict(schema_version=True), id='boolean-schema-version'),
    pytest.param(dict(recommendation_only=False), id='recommendation-only-disabled'),
    pytest.param(dict(limits={'seconds': float('nan')}), id='nan-seconds'),
    pytest.param(dict(limits={'trials': True}), id='boolean-trials'),
    pytest.param(dict(harness_paths=['../escape']), id='escaping-path'),
    pytest.param(dict(harness_paths=['.git/config']), id='git-path'),
    pytest.param(dict(profile='isolated'), id='isolated-profile'),
    pytest.param(dict(runtime={'name': 'codex', 'executable': 'codex'}), id='unpinned-runtime'),
])
def test_unknown_keys_bad_limits_paths_and_policy_are_rejected(changes):
    with pytest.raises((ValueError, TypeError)): config.prepare(changes)


def test_unknown_key_in_a_written_config_fails_doctor_without_echoing_it(tmp_path):
    (tmp_path/'ahl.json').write_text('{"token":"SECRET_CANARY"}')
    code, output, error = invoke('doctor', str(tmp_path), '--json')
    assert code == 2
    assert 'SECRET_CANARY' not in output + error


def test_harness_paths_select_classified_surfaces():
    surfaces = ['AGENTS.md', 'app/CLAUDE.md', '.claude/CLAUDE.md', 'app/AGENTS.override.md', '.claude/rules/x.md',
                '.claude/agents/r.md', '.codex/agents/r.toml', '.claude/commands/c.md', 'pkg/.claude/skills/d/SKILL.md',
                '.claude/skills/d/SKILL.md', 'solve.py']
    assert config.prepare(dict(harness_paths=surfaces))['harness_paths'] == surfaces


@pytest.mark.parametrize('private', [
    pytest.param('CLAUDE.local.md', id='local-instructions'),
    pytest.param('app/CLAUDE.local.md', id='nested-local-instructions'),
    pytest.param('.claude/settings.local.json', id='local-settings'),
    pytest.param('.claude/agent-memory/r/MEMORY.md', id='agent-memory'),
    pytest.param('skills/d/__pycache__/m.pyc', id='bytecode-cache'),
])
def test_harness_paths_select_classified_surfaces_but_never_private_files(private):
    with pytest.raises(ValueError, match='unsafe configured path'):
        config.prepare(dict(harness_paths=['AGENTS.md', private]))


def test_symlink_hardlink_and_project_alias_are_rejected(tmp_path):
    source = tmp_path/'settings.json'; source.write_text('{}')
    link = tmp_path/'link.json'; link.symlink_to(source)
    with pytest.raises(ValueError): config.read_json(link)
    link.unlink(); os.link(source, link)
    with pytest.raises(ValueError): config.read_json(link)
    project = tmp_path/'project'; project.mkdir()
    alias = tmp_path/'alias'; alias.symlink_to(project, target_is_directory=True)
    assert doctor(alias)['status'] == 'blocked'
    with pytest.raises(ValueError): config.create(alias, config.defaults())
    (project/'ahl.json').symlink_to(source)
    with pytest.raises(FileExistsError): config.create(project, config.defaults())
    assert source.read_text() == '{}'


def test_duplicate_json_keys_and_failed_atomic_write_leave_no_partial_config(tmp_path):
    settings = tmp_path/'input.json'; settings.write_text('{"profile":"offline","profile":"isolated"}')
    with pytest.raises(ValueError): config.read_json(settings)
    real_fsync = os.fsync
    calls = []
    def fail_directory_sync(fd):
        calls.append(fd)
        if len(calls) == 2:
            raise OSError('synthetic directory sync failure')
        real_fsync(fd)
    with patch.object(config.os, 'fsync', side_effect=fail_directory_sync):
        with pytest.raises(OSError): config.create(tmp_path, config.defaults())
    assert sorted(p.name for p in tmp_path.iterdir()) == ['input.json']


def test_missing_assets_and_modified_bank_are_visible(tmp_path):
    with patch('agent_harness_lab.cli.assets', return_value=tmp_path):
        assert doctor(tmp_path)['status'] == 'blocked'
    value = config.defaults(); value['bank']['sha256'] = '0'*64
    config.create(tmp_path, value)
    report = doctor(tmp_path)
    assert next(c['status'] for c in report['checks'] if c['check_id'] == 'bank') == 'blocked'


def test_doctor_does_not_read_bank_fixture_or_oracle_bodies(tmp_path):
    bank_root = tmp_path/'bank'
    shutil.copytree(config.starter().parent, bank_root)
    bank = bank_root/'bank.json'
    canary = bank_root/'tasks/localization/.auth'
    canary.write_text('SYNTHETIC_AUTH_CANARY')
    config.create(tmp_path, config.prepare({'bank': {'path': 'bank/bank.json',
                     'sha256': hashlib.sha256(bank.read_bytes()).hexdigest()}}))
    original = Path.read_bytes
    def bounded_metadata(path):
        if path.is_relative_to(bank_root) and path != bank:
            raise AssertionError('doctor must not open bank data bodies')
        return original(path)
    with patch.object(Path, 'read_bytes', bounded_metadata), \
            patch('agent_harness_lab.evaluation.task_bank.digest', side_effect=AssertionError('no tree hashing')):
        code, output, error = invoke('doctor', str(tmp_path), '--json')
    assert code == 0, error
    assert 'SYNTHETIC_AUTH_CANARY' not in output+error
    report = json.loads(output)
    assert next(c['status'] for c in report['checks'] if c['check_id'] == 'bank_contents') == 'not_checked'
    from agent_harness_lab.evaluation.task_bank import validate
    with pytest.raises(ValueError, match='integrity'): validate(bank)
    assert not validate(bank, verify_contents=False)['certification_eligible']


def test_unsupported_filesystem_capabilities_block_without_mutation(tmp_path):
    with patch.object(config, 'supported', return_value=False):
        assert invoke('doctor', str(tmp_path), '--json')[0] == 3
        assert invoke('init', str(tmp_path), '--non-interactive', '--write')[0] == 3
        with pytest.raises(NotImplementedError): config.create(tmp_path, config.defaults())
    assert list(tmp_path.iterdir()) == []


def test_unsupported_python_is_reported(tmp_path):
    with patch('agent_harness_lab.onboarding.sys.version_info', (3, 10, 0)):
        report = doctor(tmp_path)
    assert next(c['status'] for c in report['checks'] if c['check_id'] == 'python') == 'blocked'


def test_bank_reader_rejects_fifo_links_oversize_and_nonobject_json(tmp_path):
    from agent_harness_lab.evaluation.task_bank import validate
    bank = tmp_path/'bank.json'
    os.mkfifo(bank)
    with pytest.raises(ValueError): validate(bank, verify_contents=False)
    bank.unlink()
    for content in ('[]', 'null', '0', ' '* (1024*1024+1)):
        bank.write_text(content)
        with pytest.raises(ValueError): validate(bank, verify_contents=False)
    alias = tmp_path/'alias.json'; os.link(bank, alias)
    with pytest.raises(ValueError): validate(alias, verify_contents=False)


def test_permission_denial_is_blocked_without_elevation_or_partial_writes(tmp_path):
    with patch.object(config, 'create', side_effect=PermissionError):
        assert invoke('init', str(tmp_path), '--non-interactive', '--write')[0] == 3
    assert list(tmp_path.iterdir()) == []
    (tmp_path/'ahl.json').write_text('{}')
    with patch.object(config, 'read_json', side_effect=PermissionError):
        assert invoke('doctor', str(tmp_path), '--json')[0] == 3


def test_explicit_probe_does_not_launch_configured_repository_executable(tmp_path):
    binary = tmp_path/'fake-agent'; binary.write_text('#!/bin/sh\ntouch should-not-exist\n'); binary.chmod(0o700)
    value = config.prepare({'runtime': {'name': 'codex', 'executable': str(binary)}})
    config.create(tmp_path, value)
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend.capability', return_value={'status': 'available'}), \
            patch('subprocess.Popen', side_effect=AssertionError('agent must not execute')):
        report = doctor(tmp_path, 'isolated', probe_runtime=True)
    assert report['status'] == 'blocked'
    assert not (tmp_path/'should-not-exist').exists()
