import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.bank_tools import export_manifest,audit_graders
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.task_bank import validate

ROOT=Path(__file__).resolve().parents[2]
BANK=ROOT/'agent_harness_lab/evaluation/fixtures/starter-v1/bank.json'


class StarterBankTests(unittest.TestCase):
    def test_all_falsifiers_and_alternative_valid_solutions_are_graded(self):
        result=audit_graders(BANK,cooperative=True)
        self.assertEqual(len(result['checks']),29)
        self.assertTrue(all(c['status']=='passed' for c in result['checks']))
        self.assertEqual(result['real_agent_trials'],'not_run')
        self.assertFalse(validate(BANK)['certification_eligible'])
        with self.assertRaises(ValueError): audit_graders(BANK)

    def test_reproducible_pack_under_private_umask_and_declared_lineage(self):
        import os
        source=ROOT/'scripts/build_synthetic_bank.py'
        spec=importlib.util.spec_from_file_location('synthetic_bank_builder',source)
        builder=importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as folder:
            old=os.umask(0o077)
            try: rebuilt=builder.build(Path(folder)/'bank')
            finally: os.umask(old)
            self.assertEqual(digest(rebuilt),digest(BANK.parent))
            tasks=json.loads(BANK.read_text())['tasks']
            calculator=[t for t in tasks if t['root_id']=='calculator']
            self.assertEqual(len(calculator),2)

    def test_manifest_export_freezes_bundles_and_respects_caller_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with self.assertRaises(ValueError): export_manifest(BANK,root/'small',max_trials=2)
            self.assertFalse((root/'small').exists())
            manifest=export_manifest(BANK,root/'export')
            self.assertEqual(len(manifest['tasks']),7)
            self.assertEqual(len(manifest['grader_self_tests']),21)
            self.assertFalse(manifest['automatic_promotion'])
            self.assertTrue(all((root/'export'/v['path']).is_dir() for v in manifest['grader_bundles'].values()))
            with self.assertRaises(ValueError): export_manifest(BANK,BANK.parent/'nested')

    def test_cli_audit_is_explicit_and_reports_unverified_independence(self):
        with patch('builtins.print') as output:
            self.assertEqual(main(['bank','--starter','--audit','--cooperative-controls']),3)
        result=json.loads(output.call_args.args[0])
        self.assertEqual(len(result['grader_audit']['checks']),29)
        self.assertFalse(result['certification_eligible'])

    def test_runner_rejects_changed_declared_grader_data_before_agent_launch(self):
        import subprocess,sys
        runner=ROOT/'skills/harness-evaluation/scripts/run_evaluation.py'
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); export_manifest(BANK,root/'export')
            (root/'baseline').mkdir(); (root/'candidate').mkdir()
            expected=root/'export/graders/localization/expected.json'
            expected.write_text('{}')
            run=subprocess.run([sys.executable,str(runner),'--manifest',str(root/'export/manifest.json'),
                '--baseline',str(root/'baseline'),'--candidate',str(root/'candidate'),
                '--output',str(root/'output'),'--codex','missing-synthetic-cli'],capture_output=True,text=True)
            self.assertEqual(run.returncode,2)
            self.assertIn('grader bundle integrity mismatch',run.stderr)
            self.assertFalse((root/'output').exists())

    def test_typed_grader_defects_remain_distinct_from_task_failure(self):
        spec=importlib.util.spec_from_file_location('typed_runner',ROOT/'skills/harness-evaluation/scripts/run_evaluation.py')
        runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
        def grade(result,code=0):
            return dict(exit_code=code,timeout=False,stdout=json.dumps(result))
        good=dict(schema_version=1,valid=True,outcome=True,grader_id='synthetic',grader_version='v1')
        self.assertEqual(runner.grading_status(grade(good),typed=True),'passed')
        self.assertEqual(runner.grading_status(grade(dict(good,outcome=False),1),typed=True),'failed')
        for broken in (grade(dict(good,valid=False),1),grade(good,1),grade({'outcome':True}),grade(dict(good,outcome=None))):
            self.assertEqual(runner.grading_status(broken,typed=True),'grader_error')

    def test_source_materialization_cannot_escape_fixture(self):
        from agent_harness_lab.evaluation.bank_tools import materialize_sources
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'source_files.json').write_text(json.dumps({'app/../../escape.py':'pass'}))
            with self.assertRaises(ValueError): materialize_sources(root)
            self.assertFalse((root.parent/'escape.py').exists())
