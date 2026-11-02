"""Live-source negative controls; dictionaries and labels never issue proof."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import agent_harness_lab
from agent_harness_lab.evaluation import controller_receipts as owned
from agent_harness_lab.sources.controller import ControllerCapture, SourceSink
from agent_harness_lab.sources.schema import digest


class ControllerSourceTests(unittest.TestCase):
    def setUp(self):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:
            self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.sink=SourceSink(self.root/'journal.json',digest('synthetic-project'),digest('synthetic-scope'))

    def test_raw_labels_and_empty_context_cannot_issue_a_source_receipt(self):
        self.assertEqual(self.sink.finish({'status':'verified'},True)['status'],'unknown')
        with self.assertRaises(ValueError): self.sink.begin(['result.txt'],time.monotonic()+5)
        with owned.session():
            capture=self.sink.begin(['result.txt'],time.monotonic()+5)
            self.assertEqual(self.sink.finish(capture,True)['status'],'unknown')
        self.assertFalse(self.sink.store.path.exists())

    def test_nested_context_exception_deadline_and_old_context_are_not_live(self):
        with owned.session():
            with self.assertRaises(TimeoutError): self.sink.begin(['result.txt'],0)
            capture=self.sink.begin(['result.txt'],time.monotonic()+5)
            with self.assertRaises(ValueError): self.sink.begin(['result.txt'],time.monotonic()+5)
            with self.assertRaises(ValueError):
                with owned.session(): pass
        with self.assertRaises(ValueError): capture.read()
        with owned.session(),self.assertRaises(ValueError): ControllerCapture(self.sink.source,capture).read(self.sink.source)

    def test_probe_does_not_execute_read_or_enable_sources(self):
        from agent_harness_lab.sources.registry import Capability, Registry
        from dataclasses import replace
        registry=Registry(); connector=ControllerCapture(self.sink.source,{})
        registry.register(Capability('controller','ahl-controller','v1',('behavioral_event','grader_reference')),connector)
        with patch.object(connector,'read',side_effect=AssertionError('no producer execution')):
            result=registry.probe(self.sink.source,**self.sink.scope)
            self.assertEqual(result['status'],'recognized'); self.assertFalse(result['source_read'])
            self.assertFalse(result['network_used']); self.assertFalse(result['execution_authorized'])
            self.assertEqual(registry.probe(replace(self.sink.source,enabled=False),**self.sink.scope)['status'],'disabled')
        with self.assertRaises(ValueError): connector.read(self.sink.source)

    def test_private_profile_cannot_be_forged_into_a_public_benchmark(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        if __package__:
            from . import test_benchmark_contracts as fixtures
        else:
            import test_benchmark_contracts as fixtures
        record={**fixtures.record(),'profile':owned.PROFILE,'publication_allowed':True}
        self.assertFalse(public_export_allowed([record]))
        self.assertFalse(public_export_allowed([{**fixtures.record(),'receipt':record}]))
        for key in ('controller_source_profile','source_capture','live_verification','verified_execution'):
            with self.subTest(key=key):
                self.assertFalse(public_export_allowed([{**fixtures.record(),key:{'publication_allowed':True}}]))

    def test_result_dictionaries_cannot_complete_a_kernel_producer_ticket(self):
        with owned.session():
            capture=self.sink.begin(['result.txt'],time.monotonic()+5)
            ticket=owned._kernel_started('executor')
            owned._kernel_spawned(ticket,{}, {}, {})
            owned._kernel_finished(ticket,{'attestation':{'verified':True}},['claimed-command'],())
            self.assertEqual(self.sink.finish(capture,True)['status'],'unknown')
        self.assertFalse(self.sink.store.path.exists())

    def test_fully_labeled_fake_backend_never_becomes_source_authority(self):
        from agent_harness_lab.evaluation import responses
        if __package__:
            from . import test_trajectory_evaluation as fixtures
        else:
            import test_trajectory_evaluation as fixtures
        f=fixtures.TrajectoryEvaluationTests(); f.setUp(); self.addCleanup(f.doCleanups); f.freeze()
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected: self.assertTrue(Path(f.f.runner.__file__).resolve().is_relative_to(Path(expected).resolve()))
        f.f.args.source_sink=self.sink
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=f.f.backend), \
                patch.object(responses,'request',side_effect=f.f.transport), \
                patch.object(f.f.runner,'execute',side_effect=AssertionError('native agents must not launch')):
            self.assertEqual(f.f.runner.evaluate(f.f.args),1)
        self.assertTrue(all(row['source_capture']['status']=='unknown' for row in f.fixture.results()['trials']))
        self.assertFalse(self.sink.store.path.exists())

    def test_source_options_require_complete_profile_scope_and_private_location(self):
        if __package__:
            from . import test_sandbox_inference as fixtures
        else:
            import test_sandbox_inference as fixtures
        f=fixtures.ResponsesEvaluationTests(); f.setUp(); self.addCleanup(f.doCleanups)
        f.args.source_cache=self.root/'journal.json'
        with self.assertRaises(ValueError): f.runner.evaluate(f.args)
        f.args.source_project_sha256=digest('synthetic-project'); f.args.source_scope_sha256=digest('synthetic-scope')
        f.args.source_cache=f.args.baseline/'journal.json'
        with self.assertRaisesRegex(ValueError,'outside all role inputs'): f.runner.evaluate(f.args)
        self.assertFalse((f.args.baseline/'journal.json').exists())


if __name__=='__main__': unittest.main()
