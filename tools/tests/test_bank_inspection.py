"""Public bank inspection remains complete and cannot issue execution authority."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.task_bank import validate
import test_evaluation_foundation as fixtures


class BankInspectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TaskBankTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def invoke(self, *options):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = main(['bank', '--manifest', str(self.fixture.path), *options])
        return code, json.loads(output.getvalue())

    def assert_analysis(self, result):
        self.assertTrue(result['analysis_only'])
        self.assertFalse(result['certification_eligible'])
        self.assertEqual(result['certification_status'], 'unverified')
        self.assertFalse(result['admission_authority'])
        self.assertFalse(result['execution_authorized'])
        self.assertNotIn('admission', result)
        self.assertNotIn('bank_qualification', result)

    def test_public_list_reads_large_manifest_all_tasks_and_long_declared_identifiers(self):
        f = self.fixture; original = f.tasks[0]
        f.tasks = [dict(original, task_id='task.'+str(index)+'x'*129,
            root_id='root.'+str(index), source_ref='synthetic '*100) for index in range(1001)]
        f.write(); self.assertGreater(f.path.stat().st_size, 1024*1024)
        code, result = self.invoke('--list')
        self.assertEqual(code, 3); self.assert_analysis(result)
        self.assertEqual(result['tasks'], 1001)
        self.assertEqual(len(result['task_index']), 1001)
        self.assertEqual(result['task_index'][-1]['task_id'], f.tasks[-1]['task_id'])
        with self.assertRaises(ValueError): validate(f.path)
        for option in ('--audit', '--export'):
            args = [option] if option == '--audit' else [option, str(f.root/'export')]
            with (patch('agent_harness_lab.evaluation.bank_tools.audit_graders', side_effect=AssertionError('no audit')),
                  patch('agent_harness_lab.evaluation.bank_tools.export_manifest', side_effect=AssertionError('no export'))):
                with self.assertRaises(SystemExit) as error: self.invoke(*args)
            self.assertEqual(error.exception.code, 2)
        self.assertFalse((f.root/'export').exists())

    def test_public_inspection_reads_large_fixture_tree_without_weakening_execution_validation(self):
        f = self.fixture; f.tasks = f.tasks[:1]; folder = f.root/'fixture0'
        for index in range(1001): (folder/str(index)).write_bytes(b'')
        (folder/'large').write_bytes(b'x'*(11*1024*1024))
        limits = dict(max_files=None, max_bytes=None, max_entries=None, max_depth=None)
        f.tasks[0]['fixture_digest'] = digest(folder, **limits); f.write()
        code, result = self.invoke()
        self.assertEqual(code, 3); self.assert_analysis(result)
        self.assertTrue(result['contents_verified'])
        self.assertGreater(result['content_grouping']['input_bytes'], 10*1024*1024)
        self.assertNotIn('task_index', result)
        with self.assertRaises(ValueError): validate(f.path)

    def test_controller_profile_is_only_a_declaration_during_inspection(self):
        from agent_harness_lab.evaluation.bank_qualification import PROFILE
        f = self.fixture; f.write(); value = json.loads(f.path.read_text())
        value['controller_profile'] = PROFILE; f.path.write_text(json.dumps(value))
        with patch('agent_harness_lab.evaluation.bank_revisions.validate_admission',
                side_effect=ValueError('controller receipt required')) as admission:
            _, result = self.invoke('--list')
            self.assert_analysis(result); admission.assert_not_called()
            self.assertEqual(result['controller_profile']['status'], 'declared_unverified')
            with self.assertRaisesRegex(ValueError, 'controller receipt required'): validate(f.path)
            admission.assert_called_once_with(f.path)

    def test_inspection_keeps_schema_path_hash_and_split_checks(self):
        f = self.fixture; original = deepcopy(f.tasks)
        cases = [dict(extra='unknown'), dict(fixture='../escape'), dict(fixture_digest='a'*64),
            dict(root_id=original[1]['root_id']), dict(partition='unsupported')]
        for change in cases:
            f.tasks = deepcopy(original); f.tasks[0].update(change); f.write()
            with self.subTest(change=change), self.assertRaises(SystemExit) as error: self.invoke()
            self.assertEqual(error.exception.code, 2)
