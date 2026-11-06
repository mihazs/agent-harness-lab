"""Public synthetic bank declarations do not issue certification authority."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import agent_harness_lab
from agent_harness_lab.cli import main
from agent_harness_lab.config import starter
from agent_harness_lab.evaluation.bank_tools import export_manifest, frozen_grader
from agent_harness_lab.evaluation.task_bank import validate


class BankQualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        original = starter(); self.original_bytes = original.read_bytes()
        self.bank = self.root/'bank'/original.name
        shutil.copytree(original.parent, self.bank.parent)
        self.value = json.loads(self.bank.read_text())

    def declarations(self, partition='certification'):
        for task in self.value['tasks']:
            task.update(review_status='accepted', independence='reviewed', reviewer='synthetic-self-label',
                        permission='approved', partition=partition)
        self.write()

    def write(self): self.bank.write_text(json.dumps(self.value))

    def test_self_labels_cannot_qualify_actual_cli_or_export(self):
        self.declarations(); stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(['bank', '--manifest', str(self.bank), '--export', str(self.root/'export')])
        result = json.loads(stdout.getvalue())
        exported = json.loads((self.root/'export/manifest.json').read_text())
        self.assertFalse(result['certification_eligible'])
        self.assertIsNone(result['content_grouping']['independent_roots'])
        self.assertFalse(result['content_grouping']['lineage_verified'])
        self.assertFalse(exported['independence_verified'])
        self.assertEqual(code, 3)
        self.assertEqual(result['certification_status'], 'unverified')
        self.assertEqual(exported['bank_certification_status'], 'unverified')
        self.assertEqual(exported['profile'], 'descriptive_smoke')
        self.assertFalse(exported['automatic_promotion'])
        self.assertEqual(starter().read_bytes(), self.original_bytes)

    def test_review_declarations_remain_distinct_from_actual_qualification(self):
        initial = validate(self.bank)
        self.assertFalse(initial['review_declarations_complete'])
        self.declarations(partition='development')
        checked = validate(self.bank)
        self.assertTrue(checked['review_declarations_complete'])
        self.assertTrue(checked['contents_verified'])
        self.assertEqual(checked['issues'], [])
        self.assertFalse(checked['certification_eligible'])
        self.assertEqual(checked['certification_reason'], 'controller_qualification_unavailable')
        self.assertEqual(checked['tasks'], 7)
        for task in self.value['tasks']:
            self.assertEqual(task['review_status'], 'accepted')
            self.assertEqual(task['independence'], 'reviewed')

    def test_forged_top_level_qualification_cannot_authorize_any_source_or_partition(self):
        for partition in ('construction', 'development', 'calibration', 'certification', 'transfer'):
            for kind in ('synthetic', 'public', 'private'):
                with self.subTest(partition=partition, kind=kind):
                    self.declarations(partition)
                    for task in self.value['tasks']: task['source_kind'] = kind
                    self.value.update(certification_eligible=True, independence_verified=True,
                                      certification_status='verified', reviewer_receipt={'verified': True})
                    self.write(); checked = validate(self.bank)
                    self.assertFalse(checked['certification_eligible'])
                    self.assertEqual(checked['certification_status'], 'unverified')
                    self.assertTrue(checked['review_declarations_complete'])

    def test_development_export_preserves_frozen_graders_artifacts_and_budget(self):
        self.declarations('development'); exported = export_manifest(self.bank, self.root/'export')
        self.assertEqual(len(exported['tasks']), 7)
        self.assertEqual(exported['max_trials'], 24)
        self.assertFalse(exported['independence_verified'])
        for task in exported['tasks']:
            bundle, artifacts = frozen_grader(self.root/'export', task, exported['grader_bundles'][task['id']])
            self.assertTrue(bundle.is_dir()); self.assertTrue(artifacts)
        with self.assertRaises(ValueError): export_manifest(self.bank, self.root/'small', max_trials=2)
        self.assertFalse((self.root/'small').exists())

    def test_legacy_boolean_result_is_not_an_export_independence_issuer(self):
        self.declarations('development'); checked = validate(self.bank)
        checked.update(certification_eligible=True)
        with patch('agent_harness_lab.evaluation.bank_tools.validate', return_value=checked):
            exported = export_manifest(self.bank, self.root/'export')
        self.assertFalse(exported['independence_verified'])
        self.assertEqual(exported['bank_certification_status'], 'unverified')

    def test_no_qualification_never_short_circuits_known_leakage_or_mutated_oracle(self):
        self.declarations()
        task = self.value['tasks'][0]
        task['partition'] = 'development'
        self.value['tasks'][1]['root_id'] = task['root_id']; self.write()
        with self.assertRaisesRegex(ValueError, 'leakage'): validate(self.bank)
        self.declarations()
        self.value['tasks'][1]['root_id'] = 'synthetic-restored-root'; self.write()
        bundle = self.bank.parent/task['grader_bundle']
        (bundle/'expected.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'integrity'): validate(self.bank)

    def test_selected_layout_import_is_actual(self):
        expected = os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected: self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))
