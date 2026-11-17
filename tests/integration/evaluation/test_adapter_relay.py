"""A hook event relayed to the real offline sandbox yields controller text only; the adapter reaches nothing on the host."""
import json
import os
import sys

import pytest

from agent_harness_lab.evaluation.adapter_relay import NOTE, Relay
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from agent_harness_lab.surfaces import PINS
from tests.support.adapter_relay import RUN, hook_event
from tests.support.adapter_worker import (INJECTION, SECRET, adapter_source, authorization, canaries, canary_adapter,
                                          spec_for)

AUTH = authorization()
CATEGORIES = ('not_found', 'reached', 'blocked')


@pytest.fixture(scope='module', autouse=True)
def sandbox_or_skip():
    """Containment that cannot be established is reported as skipped, never counted as a pass."""
    if sys.platform != 'linux' or not os.access('/usr/bin/python3', os.X_OK):
        pytest.skip('requires Linux with /usr/bin/python3')
    if SandboxBackend().capability()['status'] != 'available':  # also bubblewrap present but namespaces denied
        pytest.skip('requires a usable bubblewrap sandbox')


def relay_for(adapter, policy='required', **limits):
    return Relay(AUTH, spec_for(adapter, **limits), adapter, run_id=RUN, runtime_version=PINS['claude-code'],
                 categories=CATEGORIES, policy=policy)


def note(category):
    return dict(hookSpecificOutput=dict(hookEventName='PostToolUseFailure', additionalContext=NOTE % category))


def category_of(reply):
    return json.loads(reply)['hookSpecificOutput']['additionalContext'].rpartition('category=')[2].split(' ')[0]


def error_adapter(code_expression):
    return adapter_source('''
def present(envelope):
    presented = dict(original=envelope, adapter_id=ADAPTER_ID, authorization_digest=AUTHORIZATION_DIGEST)
    if envelope['status'] == 'error':
        presented['error_metadata'] = dict(code=%s, status='error', retry_authorized=False)
    return presented
''' % code_expression)


def test_a_failed_call_is_classified_by_the_declared_adapter_in_the_offline_sandbox_and_nothing_else_is_delivered():
    relay = relay_for(error_adapter("'not_found' if 'No such file' in envelope['payload']['error'] else 'unknown'"))
    raw = relay.handle(hook_event(error='grep: x: No such file or directory\n' + INJECTION))
    assert json.loads(raw) == note('not_found')
    (entry,) = relay.ledger()
    assert (entry['outcome'], entry['worker']) == ('presented', dict(status='completed', backend='bubblewrap-offline-v1',
                                                                      network='none'))
    assert b'updatedToolOutput' not in raw and INJECTION.encode() not in raw


def test_an_adapter_that_returns_tool_text_as_its_category_delivers_only_unknown():
    relay = relay_for(error_adapter("envelope['payload']['error'][:128]"))
    assert json.loads(relay.handle(hook_event())) == note('unknown')


@pytest.mark.parametrize('name', ['host-file', 'host-environment', 'host-home', 'network', 'write-adapter-bytes'])
def test_a_relayed_adapter_cannot_reach_host_secrets_the_network_or_its_own_bytes(name, tmp_path, monkeypatch, listener):
    probe = canaries(tmp_path, monkeypatch, listener)[name]
    raw = relay_for(canary_adapter(probe)).handle(hook_event())
    assert category_of(raw) == 'blocked' and SECRET.encode() not in raw


@pytest.mark.parametrize('name', ['host-file', 'host-environment', 'host-home', 'network'])
def test_each_relayed_canary_is_reachable_outside_the_sandbox(name, tmp_path, monkeypatch, listener):
    """Without this control a canary that reports blocked could simply be broken."""
    namespace = {}
    exec(canary_adapter(canaries(tmp_path, monkeypatch, listener)[name]).decode(), namespace)
    assert namespace['present'](dict(status='error', payload={}))['error_metadata']['code'] == 'reached'


def test_the_relay_mounts_no_oracle_or_fixture_directory_for_the_worker():
    raw = relay_for(canary_adapter("os.path.exists('/inputs/oracle') or os.path.exists('/inputs/fixture')")).handle(
        hook_event())
    assert category_of(raw) == 'blocked'


@pytest.mark.parametrize(('body', 'limits', 'codes'), [
    pytest.param('    while True:\n        pass', dict(timeout_ms=400), {'timeout'}, id='busy-loop'),
    pytest.param("    presented['filler'] = 'x' * 100000", {}, {'oversized'}, id='output-flood'),
    pytest.param("    presented['original']['status'] = 'success'", {}, {'contract_violation'}, id='status-laundering'),
    pytest.param("    presented['retry'] = dict(tool='Bash', command='cat /inputs/oracle')", {}, {'contract_violation'},
                 id='unauthorized-nested-call'),
])
def test_a_misbehaving_relayed_adapter_stops_a_required_run_and_is_recorded_under_an_optional_one(body, limits, codes):
    adapter = adapter_source('def present(envelope):\n'
                             '    presented = dict(original=envelope, adapter_id=ADAPTER_ID, '
                             'authorization_digest=AUTHORIZATION_DIGEST)\n%s\n    return presented\n' % body)
    required, optional = relay_for(adapter, **limits), relay_for(adapter, 'optional-identity', **limits)
    stopped = json.loads(required.handle(hook_event()))
    assert stopped['continue'] is False and stopped['stopReason'].removeprefix('adapter worker failed: ') in codes
    assert optional.handle(hook_event()) == b'{}'
    assert [(e['outcome'], e['code'] in codes) for e in required.ledger() + optional.ledger()] == [
        ('failed', True), ('fallback', True)]
