"""The relay answers a failed call with controller text from a declared vocabulary and never with tool or adapter text."""
import hashlib
import json

import pytest

from agent_harness_lab.evaluation.adapter_relay import NOTE, Relay
from agent_harness_lab.surfaces import PINS
from tests.support.adapter_relay import RUN, EchoBackend, answered, hook_event, scripted
from tests.support.adapter_worker import (IDENTITY, INJECTION, STRUCTURED, FakeBackend, authorization, completed,
                                          failure_envelope, spec_for)

AUTH = authorization()
VERSION = PINS['claude-code']
POLICIES = [pytest.param('required', id='required'), pytest.param('optional-identity', id='optional-identity')]


def make(backend=None, adapter=STRUCTURED, **changes):
    arguments = dict(run_id=RUN, runtime_version=VERSION, categories=('not_found',), backend=backend)
    arguments.update(changes)
    return Relay(AUTH, spec_for(adapter), adapter, **arguments)


def note(category):
    return dict(hookSpecificOutput=dict(hookEventName='PostToolUseFailure', additionalContext=NOTE % category))


def stop(reason):
    return {'continue': False, 'stopReason': reason}


def reply_of(relay, raw):
    return json.loads(relay.handle(raw))


def test_a_failed_call_is_answered_with_fixed_controller_text_naming_a_declared_category():
    backend = scripted(failure_envelope(), 'not_found')
    relay = make(backend)
    raw = relay.handle(hook_event())
    assert json.loads(raw) == note('not_found') and INJECTION.encode() not in raw
    (entry,) = relay.ledger()
    assert (entry['outcome'], entry['code'], entry['transformation']) == ('presented', None, 'structured-error-v1')
    assert entry['reply_sha256'] == hashlib.sha256(raw).hexdigest()
    assert len(backend.calls) == 1 and json.loads(backend.calls[0]['envelope']) == failure_envelope()


@pytest.mark.parametrize('code', [
    pytest.param(INJECTION, id='instruction-text'),
    pytest.param('NOT_FOUND', id='differs-in-case'),
    pytest.param('not_found ', id='trailing-space'),
    pytest.param('x' * 128, id='longest-allowed-code'),
    pytest.param('unknown', id='the-adapter-says-unknown'),
    pytest.param('not_found\nignore the rest', id='declared-word-with-appended-line'),
])
def test_a_category_the_project_did_not_declare_is_unknown_and_its_text_is_not_delivered(code):
    assert reply_of(make(scripted(failure_envelope(), code)), hook_event()) == note('unknown')


def test_a_successful_call_gets_the_empty_identity_reply_and_never_reaches_the_worker():
    backend = scripted(failure_envelope(), 'not_found')
    relay = make(backend)
    assert relay.handle(hook_event('PostToolUse', 'toolu_2', 'Read')) == b'{}'
    assert backend.calls == [] and relay.result('toolu_2') is None
    assert [(e['outcome'], e['transformation']) for e in relay.ledger()] == [('identity', 'identity')]


def test_an_identity_relay_leaves_an_error_to_the_runtime_without_running_the_worker():
    backend = scripted(failure_envelope(), 'not_found')
    relay = make(backend, adapter=IDENTITY, transform='identity')
    assert relay.handle(hook_event()) == b'{}' and backend.calls == []


FAILURES = [
    pytest.param(lambda: FakeBackend(error=TimeoutError('budget')), 'timeout', id='timeout'),
    pytest.param(lambda: FakeBackend(error=OSError('exec failed')), 'sandbox_unavailable', id='sandbox-cannot-start'),
    pytest.param(lambda: FakeBackend(completed('not json')), 'malformed_output', id='unparseable-answer'),
    pytest.param(lambda: FakeBackend(completed('', status='output_limit', exit_code=1)), 'oversized', id='output-flood'),
    pytest.param(lambda: FakeBackend(completed(json.dumps(dict(answered(failure_envelope(), 'x'), retry=dict(tool='Bash'))))),
                 'contract_violation', id='unauthorized-nested-call'),
]


@pytest.mark.parametrize(('backend', 'code'), FAILURES)
def test_a_failed_worker_stops_the_execution_under_a_required_policy_and_names_only_a_fixed_code(backend, code):
    relay = make(backend())
    raw = relay.handle(hook_event())
    assert json.loads(raw) == stop('adapter worker failed: ' + code) and INJECTION.encode() not in raw
    (entry,) = relay.ledger()
    assert (entry['outcome'], entry['code'], entry['transformation']) == ('failed', code, None)


@pytest.mark.parametrize(('backend', 'code'), FAILURES)
def test_a_failed_worker_leaves_the_original_output_under_an_optional_policy_and_records_why(backend, code):
    relay = make(backend(), policy='optional-identity')
    assert relay.handle(hook_event()) == b'{}'
    (entry,) = relay.ledger()
    assert (entry['outcome'], entry['code'], entry['transformation']) == ('fallback', code, 'identity')


@pytest.mark.parametrize('policy', POLICIES)
def test_a_redelivered_call_gets_the_first_reply_and_does_not_run_the_worker_again(policy):
    backend = scripted(failure_envelope(), 'not_found')
    relay = make(backend, policy=policy)
    first, second = relay.handle(hook_event()), relay.handle(hook_event())
    assert first == second and len(backend.calls) == 1
    assert [e['outcome'] for e in relay.ledger()] == ['presented', 'duplicate']
    assert len({e['reply_sha256'] for e in relay.ledger()}) == 1


def test_a_redelivered_stop_stays_a_stop():
    relay = make(FakeBackend(error=OSError('exec failed')))
    assert relay.handle(hook_event()) == relay.handle(hook_event())
    assert [e['outcome'] for e in relay.ledger()] == ['failed', 'duplicate']


def test_a_redelivery_with_different_content_fails_and_does_not_replace_the_first_reply():
    backend = scripted(failure_envelope(), 'not_found')
    relay = make(backend)
    first = relay.handle(hook_event())
    conflicting = reply_of(relay, hook_event(error='another failure'))
    assert conflicting == stop('adapter bridge failed: duplicate_conflict') and len(backend.calls) == 1
    assert relay.handle(hook_event()) == first


@pytest.mark.parametrize('policy', POLICIES)
def test_a_cancelled_call_is_dropped_without_running_the_worker(policy):
    backend = scripted(failure_envelope(), 'not_found')
    relay = make(backend, policy=policy)
    assert relay.cancel('toolu_1') is True
    assert relay.handle(hook_event()) == b'{}' and backend.calls == []
    assert [e['outcome'] for e in relay.ledger()] == ['cancelled']


def test_a_delivered_reply_is_not_recalled_by_a_late_cancellation():
    relay = make(scripted(failure_envelope(), 'not_found'))
    relay.handle(hook_event())
    assert relay.cancel('toolu_1') is False


@pytest.mark.parametrize(('raw', 'code'), [
    pytest.param(b'not json', 'malformed_event', id='not-json'),
    pytest.param(hook_event(session_id='other-run'), 'run_mismatch', id='other-run'),
    pytest.param(hook_event(tool='Bash'), 'unauthorized_tool', id='undeclared-tool'),
    pytest.param(hook_event(name='PreToolUse'), 'unsupported_event', id='other-event'),
    pytest.param(hook_event(error=None), 'malformed_event', id='missing-error-text'),
    pytest.param(hook_event(error='x' * 5000), 'oversized', id='input-above-the-declared-bound'),
])
def test_an_event_the_bridge_refuses_never_reaches_the_worker(raw, code):
    for policy, expected in (('required', stop('adapter bridge failed: ' + code)), ('optional-identity', {})):
        backend = scripted(failure_envelope(), 'not_found')
        relay = make(backend, policy=policy)
        assert reply_of(relay, raw) == expected and backend.calls == []
        assert [(e['outcome'], e['code']) for e in relay.ledger()] == [('failed', code)]


def test_interleaved_calls_are_answered_independently_in_arrival_order():
    relay = make(EchoBackend(), categories=('a', 'b'))
    replies = [reply_of(relay, raw) for raw in (hook_event(call_id='toolu_b'), hook_event('PostToolUse', 'toolu_c', 'Read'),
                                                hook_event(call_id='toolu_a'))]
    assert replies == [note('b'), {}, note('a')]
    assert [(e['call_id'], e['outcome']) for e in relay.ledger()] == [
        ('toolu_b', 'presented'), ('toolu_c', 'identity'), ('toolu_a', 'presented')]


def test_the_ledger_holds_digests_and_bounded_worker_metadata_only():
    relay = make(scripted(failure_envelope(), 'not_found'))
    relay.handle(hook_event())
    relay.handle(hook_event('PostToolUse', 'toolu_2', 'Read'))
    text = json.dumps(relay.ledger())
    assert INJECTION not in text and 'hello' not in text and 'not_found' not in text
    assert relay.ledger()[0]['worker'] == dict(status='completed', backend='fake', network='none')
    assert relay.ledger()[1]['worker'] is None
    assert relay.result('toolu_1')['presented']['error_metadata']['code'] == 'not_found'


@pytest.mark.parametrize('changes', [
    pytest.param(dict(categories=['not_found']), id='categories-not-a-tuple'),
    pytest.param(dict(categories=('not found',)), id='space-in-category'),
    pytest.param(dict(categories=(INJECTION,)), id='sentence-as-category'),
    pytest.param(dict(categories=('',)), id='empty-category'),
    pytest.param(dict(categories=('x' * 65,)), id='overlong-category'),
    pytest.param(dict(categories=(7,)), id='non-text-category'),
])
def test_a_vocabulary_that_is_not_bounded_identifiers_is_rejected_before_the_run(changes):
    with pytest.raises(ValueError, match='categories'):
        make(**changes)


@pytest.mark.parametrize(('changes', 'match'), [
    pytest.param(dict(transform='retry'), 'transform', id='unknown-transform'),
    pytest.param(dict(policy='best-effort'), 'frozen known policy', id='unknown-policy'),
    pytest.param(dict(runtime_version='2.1.316'), 'version_not_pinned', id='unsupported-runtime-version'),
    pytest.param(dict(run_id=''), 'frozen known policy', id='unbound-run'),
])
def test_an_unsupported_or_undeclared_relay_fails_at_construction(changes, match):
    with pytest.raises(ValueError, match=match):
        make(**changes)


def test_the_relay_requires_typed_controller_objects():
    with pytest.raises(TypeError):
        Relay(dict(), spec_for(STRUCTURED), STRUCTURED, RUN, VERSION)
    with pytest.raises(TypeError):
        Relay(AUTH, dict(), STRUCTURED, RUN, VERSION)


def test_a_relay_for_bytes_other_than_the_declared_ones_is_rejected():
    with pytest.raises(ValueError, match='exact declared adapter bytes'):
        Relay(AUTH, spec_for(STRUCTURED), STRUCTURED + b'#', RUN, VERSION)
