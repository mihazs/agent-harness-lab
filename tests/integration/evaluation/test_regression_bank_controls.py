"""Synthetic reviewed controls stay outside immutable bank oracle bundles."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock, patch

import pytest

import agent_harness_lab
from agent_harness_lab.evaluation import bank_tools
from agent_harness_lab.evaluation.integrity import digest


class ReviewedBank:
    """A synthetic regression bank whose five reviewed controls live outside its oracle bundle."""

    def __init__(self, folder):
        self.root = folder/'bank'; self.root.mkdir()
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
        self.output = folder/'export'
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


@pytest.fixture
def bank(tmp_path):
    return ReviewedBank(tmp_path)


def test_export_materializes_all_five_controls_without_changing_either_oracle(bank):
    frozen = digest(bank.root/'oracle')
    with bank.controlled(): manifest = bank_tools.export_manifest(bank.path,bank.output)
    assert manifest['bank_revision_sha256'] == 'a'*64
    assert manifest['grader_bundles'][bank.task['task_id']]['digest'] == frozen
    assert digest(bank.output/'graders/regression') == frozen
    assert digest(bank.root/'oracle') == frozen
    assert len(list((bank.output/'controls/regression').iterdir())) == 5
    assert {c['state'] for c in manifest['grader_self_tests']} == {'broken','partial','complete'}
    for index, control in enumerate(bank.controls):
        assert digest(control['fixture']) == control['digest']
        assert not (control['fixture']/'app/synthetic.py').exists()
        assert (bank.output/'controls/regression'/str(index)/'app/synthetic.py').is_file()
    assert all(c['fixture'].startswith('controls/regression/') for c in manifest['grader_self_tests'])


def test_unchanged_legacy_parent_controls_also_export_outside_the_oracle(bank):
    original_task = next(t for t in json.loads((bank.original/'bank.json').read_text())['tasks'] if t['task_id']=='regression')
    shutil.rmtree(bank.root/'oracle')
    shutil.copytree(bank.original/original_task['grader_bundle'],bank.root/'oracle')
    (bank.root/'oracle/controls/broken/source_files.json').write_text(json.dumps({'app/parent-only.py':'pass\n'}))
    bank.task['grader_digest'] = digest(bank.root/'oracle'); bank.write_bank()
    controls = [dict(state=state,fixture=bank.root/'oracle/controls'/state,
                     digest=digest(bank.root/'oracle/controls'/state))
                for state in ('broken','partial','complete','alternative_valid')]
    with bank.controlled(controls=controls):
        manifest = bank_tools.export_manifest(bank.path,bank.output)
    assert len(list((bank.output/'controls/regression').iterdir())) == 4
    assert manifest['grader_bundles']['regression']['digest'] == bank.task['grader_digest']
    assert not (bank.output/'graders/regression/controls/broken/app/parent-only.py').exists()
    assert (bank.output/'controls/regression/0/app/parent-only.py').is_file()
    assert digest(bank.root/'oracle') == bank.task['grader_digest']


def test_cooperative_audit_uses_all_reviewed_controls_and_preserves_raw_inputs(bank):
    frozen = digest(bank.root/'oracle')
    with bank.controlled(): result = bank_tools.audit_graders(bank.path,cooperative=True)
    assert result['status'] == 'controls_passed'
    assert [c['control'] for c in result['checks']] == ['no_op'] + [c['state'] for c in bank.controls]
    assert digest(bank.root/'oracle') == frozen
    assert all(digest(c['fixture'])==c['digest'] for c in bank.controls)


def test_sandbox_audit_uses_pristine_oracle_and_separate_materialized_artifacts(bank):
    backend = Mock(); backend.capability.return_value = {'status':'available'}
    def execute(argv, workspace, inputs, role, limits):
        assert role == 'grader'; assert digest(inputs['oracle']) == bank.task['grader_digest']
        translated = [a.replace('/inputs/oracle',str(inputs['oracle'])).replace('/inputs/fixture',str(inputs['fixture'])) for a in argv]
        run = subprocess.run(translated,cwd=workspace,capture_output=True,text=True,timeout=limits.seconds)
        return dict(status='completed' if run.returncode==0 else 'failed',exit_code=run.returncode,
                    stdout=run.stdout,stderr=run.stderr,inputs_unchanged=True)
    backend.run.side_effect = execute
    with bank.controlled(),patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=backend):
        result = bank_tools.audit_graders(bank.path,sandbox=True)
    assert result['status'] == 'controls_passed'; assert backend.run.call_count == 6


def test_ineligible_revision_refuses_export_and_audit_before_any_side_effect(bank):
    with bank.controlled(admission={**bank.admission,'current_eligible':False}), \
         patch.object(bank_tools.subprocess,'run',side_effect=AssertionError('no grader launch')), \
         patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',side_effect=AssertionError('no backend launch')):
        with pytest.raises(ValueError, match='eligible'): bank_tools.export_manifest(bank.path,bank.output)
        with pytest.raises(ValueError, match='eligible'): bank_tools.audit_graders(bank.path,sandbox=True)
    assert not bank.output.exists()


def test_late_control_integrity_failure_precedes_output_or_grader_launch(bank):
    (bank.controls[-1]['fixture']/'original.txt').write_text('changed raw control\n')
    with bank.controlled(),patch.object(bank_tools.subprocess,'run',side_effect=AssertionError('no grader launch')):
        with pytest.raises(ValueError, match='identity changed'): bank_tools.export_manifest(bank.path,bank.output)
        with pytest.raises(ValueError, match='identity changed'): bank_tools.audit_graders(bank.path,cooperative=True)
    assert not bank.output.exists()


def test_controlled_bank_never_falls_back_to_embedded_controls(bank):
    with bank.controlled(controls=[]), pytest.raises(ValueError, match='reviewed controls'):
        bank_tools.export_manifest(bank.path,bank.output)
    assert not bank.output.exists()


def test_oracle_change_after_validation_cannot_rebaseline_an_audit(bank):
    def changed(*args):
        (bank.root/'oracle/expected.json').write_text('{}')
        return bank.controls
    with bank.controlled() as resolver, patch.object(bank_tools.subprocess,'run') as launch:
        resolver.side_effect = changed
        with pytest.raises(ValueError, match='oracle identity changed'):
            bank_tools.audit_graders(bank.path,cooperative=True)
    launch.assert_not_called()


def test_revocation_during_export_prevents_publishing_a_manifest(bank):
    calls = [0]
    def checked(path):
        calls[0] += 1
        result = bank.validate(path)
        result['admission'] = {**bank.admission,'current_eligible':calls[0]==1}
        return result
    with bank.controlled(),patch.object(bank_tools,'validate',side_effect=checked):
        with pytest.raises(ValueError, match='eligible'):
            bank_tools.export_manifest(bank.path,bank.output)
    assert not (bank.output/'manifest.json').exists()


def test_legacy_bank_keeps_embedded_controls_and_never_loads_revision_helper(bank):
    original_task = next(t for t in json.loads((bank.original/'bank.json').read_text())['tasks'] if t['task_id']=='regression')
    shutil.rmtree(bank.root/'oracle')
    shutil.copytree(bank.original/original_task['grader_bundle'],bank.root/'oracle')
    bank.task['grader_digest'] = digest(bank.root/'oracle'); bank.write_bank()
    with patch.object(bank_tools,'_reviewed_controls',side_effect=AssertionError('legacy requires no revision helper')):
        manifest = bank_tools.export_manifest(bank.path,bank.output)
        result = bank_tools.audit_graders(bank.path,cooperative=True)
    assert 'bank_revision_sha256' not in manifest
    assert all(c['fixture'].startswith('graders/regression/') for c in manifest['grader_self_tests'])
    assert not (bank.output/'controls').exists(); assert result['status'] == 'controls_passed'
