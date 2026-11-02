"""Relative bank exports retain frozen oracles and reject unsafe destinations."""
from contextlib import chdir
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import agent_harness_lab
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.bank_tools import export_manifest, frozen_grader


class BankExportPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
        shutil.copytree(source,self.root/'input')

    def test_relative_source_and_nested_destination_produce_usable_frozen_manifest(self):
        with chdir(self.root):
            manifest = export_manifest('input/bank.json','nested/export')
        output = self.root/'nested/export'
        self.assertEqual(json.loads((output/'manifest.json').read_text()),manifest)
        for task in manifest['tasks']:
            bundle,allowed = frozen_grader(output,task,manifest['grader_bundles'][task['id']])
            self.assertTrue(bundle.is_dir()); self.assertTrue(allowed)
            self.assertTrue((output/task['fixture']).is_dir())
        for control in manifest['grader_self_tests']:
            self.assertTrue((output/control['fixture']).is_dir())

    def test_cli_relative_destination_exports_without_upgrading_independence(self):
        with chdir(self.root), patch('builtins.print') as printed:
            code = main(['bank','--manifest','input/bank.json','--export','export'])
        self.assertEqual(code,3)
        result = json.loads(printed.call_args.args[0])
        self.assertFalse(result['certification_eligible'])
        self.assertEqual(result['export']['tasks'],7)
        self.assertTrue((self.root/'export/manifest.json').is_file())

    def test_parent_traversal_cannot_hide_destination_inside_source(self):
        (self.root/'alias').mkdir()
        with chdir(self.root), self.assertRaises(ValueError):
            export_manifest('input/bank.json','alias/../input/export')
        self.assertFalse((self.root/'input/export').exists())

    def test_symlink_source_overlap_and_existing_output_refuse_without_writes(self):
        (self.root/'link').symlink_to(self.root,target_is_directory=True)
        existing = self.root/'existing'; existing.mkdir(); (existing/'keep').write_text('preserve')
        with chdir(self.root):
            for output in ('link/export','input/export','existing'):
                with self.subTest(output=output), self.assertRaises((ValueError,FileExistsError)):
                    export_manifest('input/bank.json',output)
        self.assertFalse((self.root/'export').exists())
        self.assertFalse((self.root/'input/export').exists())
        self.assertEqual([p.name for p in existing.iterdir()],['keep'])
        self.assertEqual((existing/'keep').read_text(),'preserve')
