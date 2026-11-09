"""Bounded startup transport and private qualification controls; no workers."""
import array
import os
from pathlib import Path
import socket
import time
from types import SimpleNamespace

import pytest

import agent_harness_lab
from agent_harness_lab.evaluation import runtime_boundary as boundary
from agent_harness_lab.promotion.policy import implementation_prerequisites


@pytest.fixture
def scratch():
    expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
    if expected:assert Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve())
    receiver,sender=socket.socketpair()
    with receiver,sender:
        deadline=time.monotonic()+.2
        yield SimpleNamespace(receiver=receiver,sender=sender,deadline=deadline,
            timeout=lambda:receiver.settimeout(max(.001,deadline-time.monotonic())))


def send(scratch,raw,*,writable=False,extra=False):
    reader,writer=os.pipe2(os.O_CLOEXEC)
    try:
        os.write(writer,len(raw).to_bytes(4,'big')+raw)
        descriptor=writer if writable else reader
        scratch.sender.sendmsg([boundary.MARKER],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[descriptor]*(2 if extra else 1)))])
    finally:os.close(reader);os.close(writer)


def test_read_end_eof_carries_one_bounded_startup_record(scratch):
    send(scratch,boundary.encoded(dict(nonce='synthetic')))
    assert boundary.receive(scratch) == dict(nonce='synthetic')


@pytest.mark.parametrize('values', [pytest.param(dict(writable=True), id='writable'),
                                    pytest.param(dict(extra=True), id='extra'),
                                    pytest.param(dict(raw=b'x'*(boundary.MAX_BYTES+1)), id='raw')])
def test_writable_extra_and_oversized_startup_descriptors_cannot_supply_facts(scratch, values):
    values=dict(values)
    raw=values.pop('raw',b'{}');send(scratch,raw,**values)
    with pytest.raises(ValueError):boundary.receive(scratch)


def test_json_and_unrequested_context_do_not_issue_protocol_qualification():
    for scope in (None,dict(restricted_process_protocol=dict(status='qualified'))):
        packet=implementation_prerequisites(scope)
        assert packet['restricted_process_protocol']['status'] == 'unknown'
        assert not packet['adoption_authority']


def test_runtime_protocol_private_profile_cannot_be_relabelled_for_export():
    from agent_harness_lab.evaluation.benchmark import public_export_allowed
    from tests.unit.evaluation import test_benchmark_contracts as fixtures
    for metadata in (dict(profile=boundary.PROFILE),dict(restricted_process_protocol=dict(status='qualified'))):
        assert not public_export_allowed([{**fixtures.record(),'receipt':metadata}])
