import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
from agent_harness_lab import config
from agent_harness_lab.onboarding import doctor


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def invoke(self, *args):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            try: code = main(list(args))
            except SystemExit as exc: code = exc.code
        return code, output.getvalue(), error.getvalue()

    def test_offline_doctor_does_not_execute_connect_or_read_auth(self):
        (self.root/'.auth').write_text('SECRET_CANARY')
        (self.root/'AGENTS.md').write_text('SECRET_CANARY')
        with patch.dict(os.environ, TOKEN='SECRET_CANARY', HOME=str(self.root)), \
                patch('subprocess.Popen', side_effect=AssertionError('no processes')), \
                patch.object(socket, 'socket', side_effect=AssertionError('no network')):
            code, output, error = self.invoke('doctor', str(self.root), '--profile', 'offline', '--json')
        self.assertEqual(code, 0, error)
        self.assertNotIn('SECRET_CANARY', output + error)
        report = json.loads(output)
        self.assertEqual(report['status'], 'ready')
        self.assertFalse(report['external_runtime_executed'])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['.auth', 'AGENTS.md'])
        self.assertEqual(next(c['status'] for c in report['checks'] if c['check_id'] == 'authentication'), 'unknown')

    def test_missing_runtime_and_isolation_block_only_required_profiles(self):
        self.assertEqual(doctor(self.root, 'offline')['status'], 'ready')
        self.assertEqual(doctor(self.root, 'cooperative')['status'], 'blocked')
        report = doctor(self.root, 'isolated')
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(next(c['status'] for c in report['checks'] if c['check_id'] == 'isolated_execution'), 'blocked')
        self.assertFalse(report['automatic_promotion'])

    def test_preview_then_exclusive_write_preserves_sentinel_and_permissions(self):
        args = ('init', str(self.root), '--non-interactive', '--json')
        code, preview, error = self.invoke(*args)
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(preview)['status'], 'preview')
        self.assertEqual(list(self.root.iterdir()), [])
        code, output, error = self.invoke(*args, '--write')
        self.assertEqual(code, 0, error)
        path = self.root/'ahl.json'; original = path.read_bytes()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.invoke(*args, '--write')[0], 2)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(doctor(self.root)['status'], 'ready')

    def test_interactive_cancel_and_non_tty_never_write(self):
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='q'):
            self.assertEqual(self.invoke('init', str(self.root), '--write')[0], 1)
        with patch('sys.stdin.isatty', return_value=False), patch('builtins.input', side_effect=AssertionError('must not wait')):
            self.assertEqual(self.invoke('init', str(self.root), '--write')[0], 2)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unknown_keys_bad_limits_paths_and_policy_are_rejected(self):
        bad = [dict(token='SECRET_CANARY'), dict(schema_version=True), dict(recommendation_only=False),
               dict(limits={'seconds': float('nan')}), dict(limits={'trials': True}),
               dict(harness_paths=['../escape']), dict(harness_paths=['.git/config']),
               dict(profile='isolated'), dict(runtime={'name': 'codex', 'executable': 'codex'})]
        for changes in bad:
            with self.subTest(changes=changes):
                with self.assertRaises((ValueError, TypeError)): config.prepare(changes)
        (self.root/'ahl.json').write_text('{"token":"SECRET_CANARY"}')
        code, output, error = self.invoke('doctor', str(self.root), '--json')
        self.assertEqual(code, 2)
        self.assertNotIn('SECRET_CANARY', output + error)

    def test_symlink_hardlink_and_project_alias_are_rejected(self):
        source = self.root/'settings.json'; source.write_text('{}')
        link = self.root/'link.json'; link.symlink_to(source)
        with self.assertRaises(ValueError): config.read_json(link)
        link.unlink(); os.link(source, link)
        with self.assertRaises(ValueError): config.read_json(link)
        project = self.root/'project'; project.mkdir()
        alias = self.root/'alias'; alias.symlink_to(project, target_is_directory=True)
        self.assertEqual(doctor(alias)['status'], 'blocked')
        with self.assertRaises(ValueError): config.create(alias, config.defaults())
        (project/'ahl.json').symlink_to(source)
        with self.assertRaises(FileExistsError): config.create(project, config.defaults())
        self.assertEqual(source.read_text(), '{}')

    def test_duplicate_json_keys_and_failed_atomic_write_leave_no_partial_config(self):
        settings = self.root/'input.json'; settings.write_text('{"profile":"offline","profile":"isolated"}')
        with self.assertRaises(ValueError): config.read_json(settings)
        real_fsync = os.fsync
        calls = []
        def fail_directory_sync(fd):
            calls.append(fd)
            if len(calls) == 2:
                raise OSError('synthetic directory sync failure')
            real_fsync(fd)
        with patch.object(config.os, 'fsync', side_effect=fail_directory_sync):
            with self.assertRaises(OSError): config.create(self.root, config.defaults())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['input.json'])

    def test_missing_assets_and_modified_bank_are_visible(self):
        with patch('agent_harness_lab.cli.assets', return_value=self.root):
            self.assertEqual(doctor(self.root)['status'], 'blocked')
        value = config.defaults(); value['bank']['sha256'] = '0'*64
        config.create(self.root, value)
        report = doctor(self.root)
        self.assertEqual(next(c['status'] for c in report['checks'] if c['check_id'] == 'bank'), 'blocked')

    def test_doctor_does_not_read_bank_fixture_or_oracle_bodies(self):
        bank_root = self.root/'bank'
        shutil.copytree(config.starter().parent, bank_root)
        bank = bank_root/'bank.json'
        canary = bank_root/'tasks/localization/.auth'
        canary.write_text('SYNTHETIC_AUTH_CANARY')
        config.create(self.root, config.prepare({'bank': {'path': 'bank/bank.json',
                         'sha256': hashlib.sha256(bank.read_bytes()).hexdigest()}}))
        original = Path.read_bytes
        def bounded_metadata(path):
            if path.is_relative_to(bank_root) and path != bank:
                raise AssertionError('doctor must not open bank data bodies')
            return original(path)
        with patch.object(Path, 'read_bytes', bounded_metadata), \
                patch('agent_harness_lab.evaluation.task_bank.digest', side_effect=AssertionError('no tree hashing')):
            code, output, error = self.invoke('doctor', str(self.root), '--json')
        self.assertEqual(code, 0, error)
        self.assertNotIn('SYNTHETIC_AUTH_CANARY', output+error)
        report = json.loads(output)
        self.assertEqual(next(c['status'] for c in report['checks'] if c['check_id'] == 'bank_contents'), 'not_checked')
        from agent_harness_lab.evaluation.task_bank import validate
        with self.assertRaisesRegex(ValueError, 'integrity'): validate(bank)
        self.assertFalse(validate(bank, verify_contents=False)['certification_eligible'])

    def test_unsupported_filesystem_capabilities_block_without_mutation(self):
        with patch.object(config, 'supported', return_value=False):
            self.assertEqual(self.invoke('doctor', str(self.root), '--json')[0], 3)
            self.assertEqual(self.invoke('init', str(self.root), '--non-interactive', '--write')[0], 3)
            with self.assertRaises(NotImplementedError): config.create(self.root, config.defaults())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unsupported_python_is_reported(self):
        with patch('agent_harness_lab.onboarding.sys.version_info', (3, 10, 0)):
            report = doctor(self.root)
        self.assertEqual(next(c['status'] for c in report['checks'] if c['check_id'] == 'python'), 'blocked')

    def test_bank_reader_rejects_fifo_links_oversize_and_nonobject_json(self):
        from agent_harness_lab.evaluation.task_bank import validate
        bank = self.root/'bank.json'
        os.mkfifo(bank)
        with self.assertRaises(ValueError): validate(bank, verify_contents=False)
        bank.unlink()
        for content in ('[]', 'null', '0', ' '* (1024*1024+1)):
            bank.write_text(content)
            with self.assertRaises(ValueError): validate(bank, verify_contents=False)
        alias = self.root/'alias.json'; os.link(bank, alias)
        with self.assertRaises(ValueError): validate(alias, verify_contents=False)

    def test_permission_denial_is_blocked_without_elevation_or_partial_writes(self):
        with patch.object(config, 'create', side_effect=PermissionError):
            self.assertEqual(self.invoke('init', str(self.root), '--non-interactive', '--write')[0], 3)
        self.assertEqual(list(self.root.iterdir()), [])
        (self.root/'ahl.json').write_text('{}')
        with patch.object(config, 'read_json', side_effect=PermissionError):
            self.assertEqual(self.invoke('doctor', str(self.root), '--json')[0], 3)

    def test_explicit_probe_does_not_launch_configured_repository_executable(self):
        binary = self.root/'fake-agent'; binary.write_text('#!/bin/sh\ntouch should-not-exist\n'); binary.chmod(0o700)
        value = config.prepare({'runtime': {'name': 'codex', 'executable': str(binary)}})
        config.create(self.root, value)
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend.capability', return_value={'status': 'available'}), \
                patch('subprocess.Popen', side_effect=AssertionError('agent must not execute')):
            report = doctor(self.root, 'isolated', probe_runtime=True)
        self.assertEqual(report['status'], 'blocked')
        self.assertFalse((self.root/'should-not-exist').exists())
