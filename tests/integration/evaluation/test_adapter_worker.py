"""The worker runs declared adapter bytes in the offline bubblewrap sandbox and contains what they can reach."""
import os
import socket
import sys

import pytest

from agent_harness_lab.evaluation.adapter_worker import present
from agent_harness_lab.evaluation.adapters import identity
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from tests.support.adapter_worker import (ADAPTER_ID, IDENTITY, INJECTION, STRUCTURED, adapter_source, authorization,
                                          canary_adapter, failure_envelope, spec_for, success_envelope)

AUTH = authorization()
TRANSFORM = 'structured-error-v1'
SECRET = 'adapter-canary-' + 'a1b2c3'


def run(adapter, envelope, *, transform=TRANSFORM, policy='required', **limits):
    return present(AUTH, spec_for(adapter, **limits), adapter, envelope, transform=transform, policy=policy)


@pytest.fixture(scope='module', autouse=True)
def sandbox_or_skip():
    """Containment that cannot be established is reported as skipped, never counted as a pass."""
    if sys.platform != 'linux' or not os.access('/usr/bin/python3', os.X_OK):
        pytest.skip('requires Linux with /usr/bin/python3')
    if SandboxBackend().capability()['status'] != 'available':  # also bubblewrap present but namespaces denied
        pytest.skip('requires a usable bubblewrap sandbox')


def test_the_identity_adapter_returns_the_exact_envelope_from_the_offline_sandbox():
    envelope = success_envelope()
    result = run(IDENTITY, envelope, transform='identity')
    assert (result['outcome'], result['code']) == ('applied', None)
    assert result['presented'] == identity(envelope, AUTH, ADAPTER_ID)
    assert (result['worker']['backend'], result['worker']['network'], result['worker']['role']) == (
        'bubblewrap-offline-v1', 'none', 'executor')


def test_the_structured_error_adapter_keeps_the_error_and_its_injection_text_verbatim():
    envelope = failure_envelope()
    result = run(STRUCTURED, envelope)
    assert (result['outcome'], result['code']) == ('applied', None)
    assert result['presented']['original'] == envelope and INJECTION in result['presented']['original']['payload']['error']
    assert result['presented']['error_metadata'] == dict(code='unknown', status='error', retry_authorized=False)


def test_a_declared_error_code_is_kept_and_an_undeclared_one_stays_unknown():
    declared = run(STRUCTURED, failure_envelope(code='ENOENT'))
    undeclared = run(STRUCTURED, failure_envelope(code=['not', 'text']))
    assert declared['presented']['error_metadata']['code'] == 'ENOENT'
    assert undeclared['presented']['error_metadata']['code'] == 'unknown'


def test_a_success_result_receives_no_error_metadata():
    result = run(STRUCTURED, success_envelope())
    assert result['outcome'] == 'applied' and set(result['presented']) == {'original', 'adapter_id', 'authorization_digest'}


def present_body(changes):
    return adapter_source('''
import time
def present(envelope):
    presented = dict(original=envelope, adapter_id=ADAPTER_ID, authorization_digest=AUTHORIZATION_DIGEST)
%s
    return presented
''' % changes)


@pytest.mark.parametrize(('body', 'limits', 'codes'), [
    pytest.param('    while True:\n        pass', dict(timeout_ms=400), {'timeout'}, id='busy-loop'),
    pytest.param("    presented['filler'] = 'x' * 100000", {}, {'oversized'}, id='output-flood'),
    pytest.param('    raise RuntimeError("boom")', {}, {'adapter_error'}, id='raises'),
    pytest.param("    presented['filler'] = bytearray(1 << 30)", {}, {'adapter_error', 'resource_limit'},
                 id='memory-flood'),
    pytest.param("    presented['original']['status'] = 'success'", {}, {'contract_violation'}, id='status-laundering'),
    pytest.param("    presented['original']['payload'] = {}", {}, {'contract_violation'}, id='payload-dropped'),
    pytest.param("    presented['original']['payload']['error'] = 'recovered'", {}, {'contract_violation'},
                 id='payload-rewritten'),
    pytest.param("    presented['retry'] = dict(tool='Bash', command='cat /inputs/oracle')", {},
                 {'contract_violation'}, id='unauthorized-nested-call'),
    pytest.param("    presented['authorization_digest'] = '0' * 64", {}, {'contract_violation'},
                 id='forged-authorization'),
    pytest.param("    return float('nan')", {}, {'adapter_error'}, id='non-finite-number'),
    pytest.param("    return [presented] * 3", {}, {'malformed_output'}, id='not-an-object'),
])
def test_a_misbehaving_adapter_fails_the_call_and_nothing_it_returns_is_delivered(body, limits, codes):
    result = run(present_body(body), failure_envelope(), **limits)
    assert (result['outcome'], result['presented']) == ('failed', None) and result['code'] in codes


@pytest.mark.parametrize('policy', [
    pytest.param('required', id='required'), pytest.param('optional-identity', id='optional-identity')])
def test_an_adapter_that_writes_an_overflowing_number_to_stdout_and_exits_is_malformed_output(policy):
    raw = adapter_source('''
import os, sys
def present(envelope):
    answer = dict(original=envelope, adapter_id=ADAPTER_ID, authorization_digest=AUTHORIZATION_DIGEST,
                  error_metadata=dict(code='unknown', status='error', retry_authorized=False))
    sys.stdout.write(json.dumps(answer).replace('"SENTINEL"', '1e999'))
    sys.stdout.flush()
    os._exit(0)
''')
    result = run(raw, failure_envelope(n='SENTINEL'), policy=policy)
    assert (result['code'], result['worker']['status']) == ('malformed_output', 'completed')
    assert result['outcome'] == {'required': 'failed', 'optional-identity': 'fallback'}[policy]


def test_an_adapter_that_writes_to_stdout_on_import_is_malformed_output():
    noisy = adapter_source("print('loading adapter')\n\ndef present(envelope):\n"
                           "    return dict(original=envelope, adapter_id=ADAPTER_ID, authorization_digest=AUTHORIZATION_DIGEST)\n")
    result = run(noisy, success_envelope(), transform='identity')
    assert (result['outcome'], result['code'], result['presented']) == ('failed', 'malformed_output', None)


def test_an_optional_policy_returns_the_identity_envelope_for_a_failing_adapter_and_records_why():
    envelope = failure_envelope()
    result = run(present_body('    raise RuntimeError("boom")'), envelope, policy='optional-identity')
    assert (result['outcome'], result['code'], result['transformation']) == ('fallback', 'adapter_error', 'identity')
    assert result['presented'] == identity(envelope, AUTH, ADAPTER_ID)


@pytest.fixture
def listener():
    server = socket.socket()
    server.bind(('127.0.0.1', 0))
    server.listen()
    yield server.getsockname()[1]
    server.close()


def canaries(tmp_path, monkeypatch, listener_port):
    secret_file = tmp_path / 'host-secret.txt'
    secret_file.write_text(SECRET)
    monkeypatch.setenv('AHL_ADAPTER_CANARY', SECRET)
    home = os.path.expanduser('~')
    return {
        'host-file': "open(%r).read() == %r" % (str(secret_file), SECRET),
        'host-environment': "os.environ.get('AHL_ADAPTER_CANARY') == %r" % SECRET,
        'host-home': "os.path.isdir(%r)" % home,
        'network': "socket.create_connection(('127.0.0.1', %d), timeout=1)" % listener_port,
        'write-adapter-bytes': "open('/inputs/harness/adapter.py', 'ab').write(b'#') or True",
    }


@pytest.mark.parametrize('name', ['host-file', 'host-environment', 'host-home', 'network', 'write-adapter-bytes'])
def test_the_adapter_cannot_reach_host_secrets_the_network_or_its_own_bytes(name, tmp_path, monkeypatch, listener):
    probe = canaries(tmp_path, monkeypatch, listener)[name]
    result = run(canary_adapter(probe), failure_envelope())
    assert (result['outcome'], result['presented']['error_metadata']['code']) == ('applied', 'blocked')


@pytest.mark.parametrize('name', ['host-file', 'host-environment', 'host-home', 'network'])
def test_each_canary_is_reachable_outside_the_sandbox(name, tmp_path, monkeypatch, listener):
    """Without this control a canary that reports blocked could simply be broken."""
    namespace = {}
    exec(canary_adapter(canaries(tmp_path, monkeypatch, listener)[name]).decode(), namespace)
    presented = namespace['present'](failure_envelope())
    assert presented['error_metadata']['code'] == 'reached'


def test_no_oracle_or_fixture_directory_is_mounted_for_the_worker():
    result = run(canary_adapter("os.path.exists('/inputs/oracle') or os.path.exists('/inputs/fixture')"),
                 failure_envelope())
    assert (result['outcome'], result['presented']['error_metadata']['code']) == ('applied', 'blocked')
