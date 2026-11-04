"""Bounded startup transport and private qualification controls; no workers."""
import array
import os
from pathlib import Path
import socket
import time
from types import SimpleNamespace
import unittest

import agent_harness_lab
from agent_harness_lab.evaluation import runtime_boundary as boundary
from agent_harness_lab.promotion.policy import implementation_prerequisites


class RuntimeBoundaryTests(unittest.TestCase):
    def setUp(self):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))
        receiver,sender=socket.socketpair()
        self.addCleanup(receiver.close);self.addCleanup(sender.close)
        deadline=time.monotonic()+.2
        self.scratch=SimpleNamespace(receiver=receiver,deadline=deadline,timeout=lambda:receiver.settimeout(max(.001,deadline-time.monotonic())))
        self.sender=sender

    def send(self,raw,*,writable=False,extra=False):
        reader,writer=os.pipe2(os.O_CLOEXEC)
        try:
            os.write(writer,len(raw).to_bytes(4,'big')+raw)
            descriptor=writer if writable else reader
            self.sender.sendmsg([boundary.MARKER],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[descriptor]*(2 if extra else 1)))])
        finally:os.close(reader);os.close(writer)

    def test_read_end_eof_carries_one_bounded_startup_record(self):
        self.send(boundary.encoded(dict(nonce='synthetic')))
        self.assertEqual(boundary.receive(self.scratch),dict(nonce='synthetic'))

    def test_writable_extra_and_oversized_startup_descriptors_cannot_supply_facts(self):
        for values in (dict(writable=True),dict(extra=True),dict(raw=b'x'*(boundary.MAX_BYTES+1))):
            with self.subTest(values=sorted(values)):
                raw=values.pop('raw',b'{}');self.send(raw,**values)
                with self.assertRaises(ValueError):boundary.receive(self.scratch)

    def test_json_and_unrequested_context_do_not_issue_protocol_qualification(self):
        for scope in (None,dict(restricted_process_protocol=dict(status='qualified'))):
            packet=implementation_prerequisites(scope)
            self.assertEqual(packet['restricted_process_protocol']['status'],'unknown')
            self.assertFalse(packet['adoption_authority'])

    def test_runtime_protocol_private_profile_cannot_be_relabelled_for_export(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        import test_benchmark_contracts as fixtures
        for metadata in (dict(profile=boundary.PROFILE),dict(restricted_process_protocol=dict(status='qualified'))):
            self.assertFalse(public_export_allowed([{**fixtures.record(),'receipt':metadata}]))
