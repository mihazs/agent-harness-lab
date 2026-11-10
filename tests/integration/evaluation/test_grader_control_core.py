"""Synthetic correction controls need no behavioral-failure declaration."""
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.regression_controls import audit_controls, STATES
from tests.integration.evaluation import test_regression_controls as control_tests


class TestGraderControlCore:
    @pytest.fixture(autouse=True)
    def _controls(self, tmp_path):
        self.emulate = control_tests.emulate
        self.root = tmp_path; self.bundle = self.root/'oracle'; self.bundle.mkdir()
        self.expected = '{"count":3,"preserved":true}'
        (self.bundle/'expected.json').write_text(self.expected)
        values = ['{"count":0,"preserved":false}', '{"count":3}', self.expected,
                  '{ "preserved": true, "count": 3 }', '{"count":3,"preserved":false}']
        self.controls = []
        for state,value in zip(STATES,values):
            folder = self.root/state; folder.mkdir(); (folder/'result.json').write_text(value)
            self.controls.append(dict(state=state,fixture=folder,digest=digest(folder)))
        self.backend = SimpleNamespace(cgroup_root=self.root/'cgroup',disk_bytes=64*1024*1024,
            image=SimpleNamespace(root=self.root/'image',sha256='e'*64),
            capability=Mock(return_value={'status':'available'}),run=Mock(side_effect=self.emulate))
        self.set_grader(structural=True)

    def set_grader(self, *, structural):
        comparison = 'json.loads(actual)==json.loads(expected)' if structural else 'actual==expected'
        source = ("import json,pathlib,sys\n"
                  "actual=(pathlib.Path(sys.argv[1])/'result.json').read_text()\n"
                  "expected=pathlib.Path(sys.argv[2]).read_text()\n"
                  f"outcome={comparison}\n"
                  "print(json.dumps(dict(schema_version=1,valid=True,outcome=outcome,grader_id='synthetic-json',grader_version='v1')))\n"
                  "sys.exit(0 if outcome else 1)\n")
        (self.bundle/'grade.py.txt').write_text(source)

    def run_core(self, **kwargs):
        started = time.monotonic()
        return audit_controls(self.bundle,digest(self.bundle),self.controls,['result.json'],'v1',
            self.root/'output',self.backend,started=started,deadline=started+60,
            integrity=lambda:True,**kwargs)

    def test_textual_defect_rejects_semantic_alternative_without_inventing_task_failure(self):
        self.set_grader(structural=False)
        result = self.run_core()
        assert result['status'] == 'controls_failed'
        checks = {row['control']:row for row in result['checks']}
        assert checks['complete']['observed'] is True
        assert checks['alternative_valid']['expected'] is True
        assert checks['alternative_valid']['observed'] is False
        assert 'task_failure_verified' not in result

    def test_structural_replacement_passes_all_controls_with_actual_identity(self):
        result = self.run_core(receipt_fields=dict(status='forged-success',checks=['forged']))
        assert result['status'] == 'grader_controls_passed'
        assert [c['observed'] for c in result['checks']] == [False,False,True,True,False]
        assert self.backend.run.call_count == 5
        for row in result['checks']:
            assert row['grader_identity'] == dict(grader_id='synthetic-json',grader_version='v1')
            assert len(row['input_sha256']) == 64
            assert len(row['materialized_sha256']) == 64
        assert (self.root/'output').stat().st_mode & 0o777 == 0o700
        assert (self.root/'output/review.json').stat().st_mode & 0o777 == 0o600

    def test_missing_runtime_dependency_is_unknown_grader_error(self):
        (self.bundle/'grade.py.txt').write_text('import synthetic_missing_dependency_628184\n')
        result = self.run_core()
        assert result['status'] == 'grader_error'
        assert result['checks'][0]['observed'] is None
        assert result['checks'][0]['grader_identity'] is None
        assert self.backend.run.call_count == 1

    def test_all_control_hashes_are_checked_before_any_dispatch(self):
        self.controls[-1]['digest'] = 'f'*64
        with pytest.raises(ValueError): self.run_core()
        self.backend.run.assert_not_called(); self.backend.capability.assert_not_called()
        assert not (self.root/'output').exists()

    def test_original_absolute_deadline_is_not_reset_by_core(self):
        with patch('agent_harness_lab.evaluation.regression_controls.time.monotonic',return_value=10):
            result = audit_controls(self.bundle,digest(self.bundle),self.controls,['result.json'],'v1',
                self.root/'output',self.backend,started=0,deadline=5,integrity=lambda:True)
        assert result['status'] == 'budget_exhausted'
        self.backend.run.assert_not_called(); self.backend.capability.assert_not_called()

    def test_controller_declaration_changes_stop_remaining_controls(self):
        def integrity():
            return self.backend.run.call_count == 0
        started = time.monotonic()
        result = audit_controls(self.bundle,digest(self.bundle),self.controls,['result.json'],'v1',
            self.root/'output',self.backend,started=started,deadline=started+60,integrity=integrity)
        assert result['status'] == 'invalid_input'
        assert self.backend.run.call_count == 1
