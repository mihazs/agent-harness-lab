"""Live-source negative controls; dictionaries and labels never issue proof."""
from contextvars import copy_context
import os
from pathlib import Path
import time
from unittest.mock import patch

import pytest

import agent_harness_lab
from agent_harness_lab.evaluation import controller_receipts as owned
from agent_harness_lab.sources.controller import ControllerCapture, SourceSink
from agent_harness_lab.sources.schema import digest
from tests.support.unittest_cleanups import run_cleanups


@pytest.fixture
def responses_evaluation():
    from tests.integration.sandbox import test_sandbox_inference as fixtures
    f=fixtures.ResponsesEvaluationTests(); f.setUp()
    try: yield f
    finally: run_cleanups(f)


class TestControllerSource:
    @pytest.fixture(autouse=True)
    def _sink(self, tmp_path):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:
            assert Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve())
        self.root=tmp_path
        self.sink=SourceSink(self.root/'journal.json',digest('synthetic-project'),digest('synthetic-scope'))

    def test_raw_labels_and_empty_context_cannot_issue_a_source_receipt(self):
        assert self.sink.finish({'status':'verified'},True)['status'] == 'unknown'
        with pytest.raises(ValueError): self.sink.begin(['result.txt'],time.monotonic()+5)
        with owned.session():
            capture=self.sink.begin(['result.txt'],time.monotonic()+5)
            assert self.sink.finish(capture,True)['status'] == 'unknown'
        assert not self.sink.store.path.exists()

    def test_nested_context_exception_deadline_and_old_context_are_not_live(self):
        with owned.session():
            with pytest.raises(TimeoutError): self.sink.begin(['result.txt'],0)
            capture=self.sink.begin(['result.txt'],time.monotonic()+5)
            with pytest.raises(ValueError): self.sink.begin(['result.txt'],time.monotonic()+5)
            with pytest.raises(ValueError):
                with owned.session(): pass
        with pytest.raises(ValueError): capture.read()
        with owned.session(),pytest.raises(ValueError): ControllerCapture(self.sink.source,capture).read(self.sink.source)

    def test_copied_closed_context_cannot_create_an_attempt(self):
        with owned.session():saved=copy_context()
        with pytest.raises(ValueError):saved.run(self.sink.begin,['result.txt'],time.monotonic()+5)

    def test_exception_exit_retires_owner_shared_with_copied_context(self):
        with pytest.raises(RuntimeError):
            with owned.session():
                saved=copy_context();owner=owned._SESSION.get()
                assert owned._live(owner)
                raise RuntimeError('synthetic controller interruption')
        assert not owned._live(owner)
        with pytest.raises(ValueError):saved.run(self.sink.begin,['result.txt'],time.monotonic()+5)

    def test_probe_does_not_execute_read_or_enable_sources(self):
        from agent_harness_lab.sources.registry import Capability, Registry
        from dataclasses import replace
        registry=Registry(); connector=ControllerCapture(self.sink.source,{})
        registry.register(Capability('controller','ahl-controller','v1',('behavioral_event','grader_reference')),connector)
        with patch.object(connector,'read',side_effect=AssertionError('no producer execution')):
            result=registry.probe(self.sink.source,**self.sink.scope)
            assert result['status'] == 'recognized'; assert not result['source_read']
            assert not result['network_used']; assert not result['execution_authorized']
            assert registry.probe(replace(self.sink.source,enabled=False),**self.sink.scope)['status'] == 'disabled'
        with pytest.raises(ValueError): connector.read(self.sink.source)

    def test_private_profile_cannot_be_forged_into_a_public_benchmark(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        from tests.unit.evaluation import test_benchmark_contracts as fixtures
        record={**fixtures.record(),'profile':owned.PROFILE,'publication_allowed':True}
        assert not public_export_allowed([record])
        assert not public_export_allowed([{**fixtures.record(),'receipt':record}])

    @pytest.mark.parametrize('key',['controller_source_profile','source_capture','live_verification','verified_execution','controller_harness_input'])
    def test_forged_private_profile_key_cannot_enter_a_public_benchmark(self,key):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        from tests.unit.evaluation import test_benchmark_contracts as fixtures
        assert not public_export_allowed([{**fixtures.record(),key:{'publication_allowed':True}}])

    def test_result_dictionaries_cannot_complete_a_kernel_producer_ticket(self):
        with owned.session():
            capture=self.sink.begin(['result.txt'],time.monotonic()+5)
            ticket=owned._kernel_started('executor')
            owned._kernel_spawned(ticket,{}, {}, {})
            owned._kernel_finished(ticket,{'attestation':{'verified':True}},['claimed-command'],())
            assert self.sink.finish(capture,True)['status'] == 'unknown'
        assert not self.sink.store.path.exists()

    def test_invalid_requested_argv_reserves_loss_before_command_validation(self):
        with owned.session():
            capture=self.sink.begin(['result.txt'],time.monotonic()+5)
            assert owned._kernel_started('executor',None) is None
            assert capture.lost
            assert len(capture.data['kernels']) == 1
            assert not capture.data['kernels'][0]['complete']
            assert self.sink.finish(capture,True)['status'] == 'unknown'

    def test_fully_labeled_fake_backend_never_becomes_source_authority(self,trajectory):
        from agent_harness_lab.evaluation import responses
        f=trajectory; f.freeze()
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected: assert Path(f.f.runner.__file__).resolve().is_relative_to(Path(expected).resolve())
        f.f.args.source_sink=self.sink
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=f.f.backend), \
                patch.object(responses,'request',side_effect=f.f.transport), \
                patch.object(f.f.runner,'execute',side_effect=AssertionError('native agents must not launch')):
            assert f.f.runner.evaluate(f.f.args) == 1
        assert all(row['source_capture']['status']=='unknown' for row in f.fixture.results()['trials'])
        assert not self.sink.store.path.exists()

    def test_source_options_require_complete_profile_scope_and_private_location(self,responses_evaluation):
        f=responses_evaluation
        f.args.source_cache=self.root/'journal.json'
        with pytest.raises(ValueError): f.runner.evaluate(f.args)
        f.args.source_project_sha256=digest('synthetic-project'); f.args.source_scope_sha256=digest('synthetic-scope')
        f.args.source_cache=f.args.baseline/'journal.json'
        with pytest.raises(ValueError, match='outside all role inputs'): f.runner.evaluate(f.args)
        assert not (f.args.baseline/'journal.json').exists()

