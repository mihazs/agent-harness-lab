"""Synthetic reviewed controls stay outside immutable bank oracle bundles."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import agent_harness_lab
from agent_harness_lab.evaluation import bank_tools
from agent_harness_lab.evaluation.integrity import digest


class ReviewedBankControlTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)/'bank'; self.root.mkdir()
        source = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
        self.original = source
        self.task = deepcopy(next(t for t in json.loads((source/'bank.json').read_text())['tasks'] if t['task_id']=='regression'))
        shutil.copytree(source/self.task['fixture'], self.root/'fixture')
        original_bundle = source/self.task['grader_bundle']
        shutil.copytree(original_bundle, self.root/'oracle')
        self.controls = []
        for index, state in enumerate(('broken','partial','complete','alternative_valid','superficial')):
            fixture = self.root/'reviewed-controls'/str(index)
            shutil.copytree(original_bundle/'controls'/('complete' if state=='superficial' else state), fixture)
            if state=='superficial': (fixture/'original.txt').write_text('damaged\n')
            (fixture/'source_files.json').write_text(json.dumps({'app/synthetic.py':'pass\n'}))
            self.controls.append(dict(state=state,fixture=fixture,digest=digest(fixture)))
        shutil.rmtree(self.root/'oracle/controls'); (self.root/'oracle/controls.json').unlink()
        self.task.update(fixture='fixture',grader_bundle='oracle',fixture_digest=digest(self.root/'fixture'),
                         grader_digest=digest(self.root/'oracle'))
        self.path = self.root/'bank.json'; self.write_bank()
        self.output = Path(temporary.name)/'export'
        self.admission = dict(revision_sha256='a'*64,current_eligible=True)
        self.validate = bank_tools.validate

    def write_bank(self):
        self.path.write_text(json.dumps(dict(schema_version=1,tasks=[self.task],separate_by=['root_id'])))

    @contextmanager
    def controlled(self, controls=None, admission=None):
        def validate(path):
            result = self.validate(path)
            result['admission'] = dict(self.admission if admission is None else admission)
            return result
        with patch.object(bank_tools,'validate',side_effect=validate), \
             patch.object(bank_tools,'_reviewed_controls',return_value=self.controls if controls is None else controls) as resolver:
            yield resolver

    def test_export_materializes_all_five_controls_without_changing_either_oracle(self):
        frozen = digest(self.root/'oracle')
        with self.controlled(): manifest = bank_tools.export_manifest(self.path,self.output)
        self.assertEqual(manifest['bank_revision_sha256'],'a'*64)
        self.assertEqual(manifest['grader_bundles'][self.task['task_id']]['digest'],frozen)
        self.assertEqual(digest(self.output/'graders/regression'),frozen)
        self.assertEqual(digest(self.root/'oracle'),frozen)
        self.assertEqual(len(list((self.output/'controls/regression').iterdir())),5)
        self.assertEqual({c['state'] for c in manifest['grader_self_tests']},{'broken','partial','complete'})
        for index, control in enumerate(self.controls):
            self.assertEqual(digest(control['fixture']),control['digest'])
            self.assertFalse((control['fixture']/'app/synthetic.py').exists())
            self.assertTrue((self.output/'controls/regression'/str(index)/'app/synthetic.py').is_file())
        self.assertTrue(all(c['fixture'].startswith('controls/regression/') for c in manifest['grader_self_tests']))

    def test_unchanged_legacy_parent_controls_also_export_outside_the_oracle(self):
        original_task = next(t for t in json.loads((self.original/'bank.json').read_text())['tasks'] if t['task_id']=='regression')
        shutil.rmtree(self.root/'oracle')
        shutil.copytree(self.original/original_task['grader_bundle'],self.root/'oracle')
        (self.root/'oracle/controls/broken/source_files.json').write_text(json.dumps({'app/parent-only.py':'pass\n'}))
        self.task['grader_digest'] = digest(self.root/'oracle'); self.write_bank()
        controls = [dict(state=state,fixture=self.root/'oracle/controls'/state,
                         digest=digest(self.root/'oracle/controls'/state))
                    for state in ('broken','partial','complete','alternative_valid')]
        with self.controlled(controls=controls):
            manifest = bank_tools.export_manifest(self.path,self.output)
        self.assertEqual(len(list((self.output/'controls/regression').iterdir())),4)
        self.assertEqual(manifest['grader_bundles']['regression']['digest'],self.task['grader_digest'])
        self.assertFalse((self.output/'graders/regression/controls/broken/app/parent-only.py').exists())
        self.assertTrue((self.output/'controls/regression/0/app/parent-only.py').is_file())
        self.assertEqual(digest(self.root/'oracle'),self.task['grader_digest'])

    def test_cooperative_audit_uses_all_reviewed_controls_and_preserves_raw_inputs(self):
        frozen = digest(self.root/'oracle')
        with self.controlled(): result = bank_tools.audit_graders(self.path,cooperative=True)
        self.assertEqual(result['status'],'controls_passed')
        self.assertEqual([c['control'] for c in result['checks']],[c['state'] for c in self.controls])
        self.assertEqual(digest(self.root/'oracle'),frozen)
        self.assertTrue(all(digest(c['fixture'])==c['digest'] for c in self.controls))

    def test_sandbox_audit_uses_pristine_oracle_and_separate_materialized_artifacts(self):
        backend = Mock(); backend.capability.return_value = {'status':'available'}
        def execute(argv, workspace, inputs, role, limits):
            self.assertEqual(role,'grader'); self.assertEqual(digest(inputs['oracle']),self.task['grader_digest'])
            translated = [a.replace('/inputs/oracle',str(inputs['oracle'])).replace('/inputs/fixture',str(inputs['fixture'])) for a in argv]
            run = subprocess.run(translated,cwd=workspace,capture_output=True,text=True,timeout=limits.seconds)
            return dict(status='completed' if run.returncode==0 else 'failed',exit_code=run.returncode,
                        stdout=run.stdout,stderr=run.stderr,inputs_unchanged=True)
        backend.run.side_effect = execute
        with self.controlled(),patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=backend):
            result = bank_tools.audit_graders(self.path,sandbox=True)
        self.assertEqual(result['status'],'controls_passed'); self.assertEqual(backend.run.call_count,5)

    def test_ineligible_revision_refuses_export_and_audit_before_any_side_effect(self):
        with self.controlled(admission={**self.admission,'current_eligible':False}), \
             patch.object(bank_tools.subprocess,'run',side_effect=AssertionError('no grader launch')), \
             patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',side_effect=AssertionError('no backend launch')):
            with self.assertRaisesRegex(ValueError,'eligible'): bank_tools.export_manifest(self.path,self.output)
            with self.assertRaisesRegex(ValueError,'eligible'): bank_tools.audit_graders(self.path,sandbox=True)
        self.assertFalse(self.output.exists())

    def test_late_control_integrity_failure_precedes_output_or_grader_launch(self):
        (self.controls[-1]['fixture']/'original.txt').write_text('changed raw control\n')
        with self.controlled(),patch.object(bank_tools.subprocess,'run',side_effect=AssertionError('no grader launch')):
            with self.assertRaisesRegex(ValueError,'identity changed'): bank_tools.export_manifest(self.path,self.output)
            with self.assertRaisesRegex(ValueError,'identity changed'): bank_tools.audit_graders(self.path,cooperative=True)
        self.assertFalse(self.output.exists())

    def test_controlled_bank_never_falls_back_to_embedded_controls(self):
        with self.controlled(controls=[]), self.assertRaisesRegex(ValueError,'reviewed controls'):
            bank_tools.export_manifest(self.path,self.output)
        self.assertFalse(self.output.exists())

    def test_oracle_change_after_validation_cannot_rebaseline_an_audit(self):
        def changed(*args):
            (self.root/'oracle/expected.json').write_text('{}')
            return self.controls
        with self.controlled() as resolver, patch.object(bank_tools.subprocess,'run') as launch:
            resolver.side_effect = changed
            with self.assertRaisesRegex(ValueError,'oracle identity changed'):
                bank_tools.audit_graders(self.path,cooperative=True)
        launch.assert_not_called()

    def test_revocation_during_export_prevents_publishing_a_manifest(self):
        calls = [0]
        def checked(path):
            calls[0] += 1
            result = self.validate(path)
            result['admission'] = {**self.admission,'current_eligible':calls[0]==1}
            return result
        with self.controlled(),patch.object(bank_tools,'validate',side_effect=checked):
            with self.assertRaisesRegex(ValueError,'eligible'):
                bank_tools.export_manifest(self.path,self.output)
        self.assertFalse((self.output/'manifest.json').exists())

    def test_legacy_bank_keeps_embedded_controls_and_never_loads_revision_helper(self):
        original_task = next(t for t in json.loads((self.original/'bank.json').read_text())['tasks'] if t['task_id']=='regression')
        shutil.rmtree(self.root/'oracle')
        shutil.copytree(self.original/original_task['grader_bundle'],self.root/'oracle')
        self.task['grader_digest'] = digest(self.root/'oracle'); self.write_bank()
        with patch.object(bank_tools,'_reviewed_controls',side_effect=AssertionError('legacy requires no revision helper')):
            manifest = bank_tools.export_manifest(self.path,self.output)
            result = bank_tools.audit_graders(self.path,cooperative=True)
        self.assertNotIn('bank_revision_sha256',manifest)
        self.assertTrue(all(c['fixture'].startswith('graders/regression/') for c in manifest['grader_self_tests']))
        self.assertFalse((self.output/'controls').exists()); self.assertEqual(result['status'],'controls_passed')
