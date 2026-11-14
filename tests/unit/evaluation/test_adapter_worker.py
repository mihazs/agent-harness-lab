"""The isolated presentation worker accepts only an answer that keeps the original envelope and adds allowed metadata."""
import copy
import json
import subprocess
from pathlib import Path

import pytest

from agent_harness_lab.evaluation import adapter_worker
from agent_harness_lab.evaluation.adapter_bridge import payload_digest
from agent_harness_lab.evaluation.adapter_worker import WORKER_SCHEMA, check_presented, present
from agent_harness_lab.evaluation.adapters import AdapterSpec, Authorization, identity, normalize_error
from agent_harness_lab.evaluation.sandbox import KeyringUnavailable
from tests.support.adapter_worker import (ADAPTER_ID, IDENTITY, INJECTION, STRUCTURED, FakeBackend, authorization,
                                          completed, failure_envelope, spec_for, success_envelope)

AUTH = authorization()
TRANSFORM = 'structured-error-v1'


def answer(envelope, transform=TRANSFORM):
    """What a conforming adapter returns: the controller's own transform of the envelope."""
    return {'identity': identity, TRANSFORM: normalize_error}[transform](envelope, AUTH, ADAPTER_ID)


def scripted(envelope, transform=TRANSFORM, **changes):
    return FakeBackend(completed(json.dumps(answer(envelope, transform)), **changes))


def run(envelope, backend, *, adapter=STRUCTURED, transform=TRANSFORM, policy='required', **limits):
    return present(AUTH, spec_for(adapter, **limits), adapter, envelope, transform=transform, policy=policy,
                   backend=backend)


def mutated(presented, edit):
    presented = copy.deepcopy(presented)
    edit(presented)
    return presented


def test_a_faithful_identity_answer_for_a_success_passes_the_check():
    envelope = success_envelope()
    assert check_presented(envelope, answer(envelope, 'identity'), AUTH, spec_for(IDENTITY), 'identity') is None


def test_a_faithful_structured_answer_for_an_error_passes_the_check():
    envelope = failure_envelope()
    assert check_presented(envelope, answer(envelope), AUTH, spec_for(STRUCTURED), TRANSFORM) is None


@pytest.mark.parametrize(('envelope', 'transform', 'edit', 'code'), [
    pytest.param(failure_envelope(), TRANSFORM, lambda p: None, 'malformed_output', id='not-an-object'),
    pytest.param(failure_envelope(), TRANSFORM, lambda p: p.update(next_call=dict(tool='Bash')), 'contract_violation',
                 id='nested-call-key'),
    pytest.param(failure_envelope(), TRANSFORM, lambda p: p.update(adapter_id='other'), 'contract_violation',
                 id='other-adapter-id'),
    pytest.param(failure_envelope(), TRANSFORM, lambda p: p.update(authorization_digest='0' * 64),
                 'contract_violation', id='other-authorization'),
    pytest.param(failure_envelope(), TRANSFORM, lambda p: p['original'].update(status='success'),
                 'contract_violation', id='error-laundered-to-success'),
    pytest.param(failure_envelope(), TRANSFORM, lambda p: p['original']['payload'].update(error='ok'),
                 'contract_violation', id='payload-edited'),
    pytest.param(failure_envelope(), TRANSFORM, lambda p: p['original'].pop('payload'), 'contract_violation',
                 id='payload-dropped'),
    pytest.param(failure_envelope(), TRANSFORM, lambda p: p.pop('error_metadata'), 'contract_violation',
                 id='metadata-missing'),
    pytest.param(failure_envelope(), TRANSFORM, lambda p: p['error_metadata'].update(retry_authorized=True),
                 'contract_violation', id='retry-authorized'),
    pytest.param(failure_envelope(), TRANSFORM, lambda p: p['error_metadata'].update(status='success'),
                 'contract_violation', id='metadata-status-laundered'),
    pytest.param(failure_envelope(), TRANSFORM, lambda p: p['error_metadata'].update(code=''), 'contract_violation',
                 id='empty-code'),
    pytest.param(failure_envelope(), TRANSFORM, lambda p: p['error_metadata'].update(code='x' * 129),
                 'contract_violation', id='oversized-code'),
    pytest.param(failure_envelope(), TRANSFORM, lambda p: p['error_metadata'].update(code=7), 'contract_violation',
                 id='non-text-code'),
    pytest.param(failure_envelope(), TRANSFORM, lambda p: p['error_metadata'].update(hint='retry'),
                 'contract_violation', id='metadata-extra-field'),
    pytest.param(success_envelope(), TRANSFORM, lambda p: p.update(error_metadata={}), 'contract_violation',
                 id='metadata-on-a-success'),
    pytest.param(failure_envelope(), 'identity', lambda p: p.update(error_metadata={}), 'contract_violation',
                 id='metadata-from-identity'),
])
def test_an_answer_that_changes_or_extends_the_envelope_is_rejected_with_a_code(envelope, transform, edit, code):
    presented = None if code == 'malformed_output' else mutated(answer(envelope, transform), edit)
    assert check_presented(envelope, presented, AUTH, spec_for(STRUCTURED), transform) == code


@pytest.mark.parametrize(('args', 'kind'), [
    pytest.param(dict(authorization=dict()), TypeError, id='untyped-authorization'),
    pytest.param(dict(spec=dict()), TypeError, id='untyped-spec'),
    pytest.param(dict(policy='best-effort'), ValueError, id='unknown-policy'),
    pytest.param(dict(transform='arbitrary-v1'), ValueError, id='unknown-transform'),
    pytest.param(dict(adapter=STRUCTURED + b'#'), ValueError, id='altered-adapter-bytes'),
    pytest.param(dict(envelope=dict(failure_envelope(), tool='Bash')), ValueError, id='tool-not-declared-by-the-spec'),
    pytest.param(dict(envelope=dict(failure_envelope(), tool='Write')), ValueError, id='tool-not-authorized'),
    pytest.param(dict(envelope=dict(call_id='x')), ValueError, id='malformed-envelope'),
])
def test_a_call_outside_the_declared_contract_raises_before_anything_runs(args, kind):
    backend = FakeBackend(completed('{}'))
    call = dict(authorization=AUTH, spec=spec_for(STRUCTURED), adapter=STRUCTURED, envelope=failure_envelope(),
                transform=TRANSFORM, policy='required')
    call.update(args)
    with pytest.raises(kind):
        present(call['authorization'], call['spec'], call['adapter'], call['envelope'], transform=call['transform'],
                policy=call['policy'], backend=backend)
    assert backend.calls == []


def test_a_transform_the_spec_does_not_declare_raises_before_anything_runs():
    only_identity = AdapterSpec.from_mapping(dict(spec_for(IDENTITY).as_mapping(), transformations=['identity']))
    backend = FakeBackend(completed('{}'))
    with pytest.raises(ValueError, match='declared transform'):
        present(AUTH, only_identity, IDENTITY, failure_envelope(), transform=TRANSFORM, backend=backend)
    assert backend.calls == []


def test_a_spec_beyond_the_project_authorization_raises_before_anything_runs():
    narrow = Authorization('project', ('Read',), (ADAPTER_ID,), 'v1')
    backend = FakeBackend(completed('{}'))
    with pytest.raises(ValueError, match='exceeds the project authorization'):
        present(narrow, spec_for(STRUCTURED), STRUCTURED, success_envelope(), transform=TRANSFORM, backend=backend)
    assert backend.calls == []


def test_an_applied_answer_is_returned_with_private_provenance_bound_to_the_call():
    envelope = failure_envelope()
    backend = scripted(envelope)
    result = run(envelope, backend)
    assert (result['schema'], result['outcome'], result['code'], result['policy']) == (WORKER_SCHEMA, 'applied', None, 'required')
    assert (result['requested'], result['transformation'], result['adapter_id'], result['call_id']) == (
        TRANSFORM, TRANSFORM, ADAPTER_ID, 'toolu_1')
    assert result['presented'] == answer(envelope)
    assert result['original_sha256'] == payload_digest(envelope)


def test_the_worker_receives_the_exact_adapter_bytes_and_envelope_under_the_declared_limits():
    envelope = failure_envelope()
    backend = scripted(envelope)
    run(envelope, backend, timeout_ms=1500, max_output_bytes=2048)
    (call,) = backend.calls
    assert call['adapter'] == STRUCTURED
    assert json.loads(call['envelope']) == envelope
    assert (call['role'], set(call['inputs'])) == ('executor', {'harness'})
    assert (call['limits'].seconds, call['limits'].output_bytes) == (1.5, 2048)
    assert call['argv'][:3] == ['/usr/bin/python3', '-I', '-B'] and call['argv'][-1] == adapter_worker.RUNNER


def test_the_private_run_directory_is_removed_after_the_run():
    backend = scripted(failure_envelope())
    run(failure_envelope(), backend)
    (call,) = backend.calls
    assert not Path(call['workspace']).exists() and not call['inputs']['harness'].exists()


@pytest.mark.parametrize(('status', 'code'), [
    pytest.param('timeout', 'timeout', id='timeout'),
    pytest.param('output_limit', 'oversized', id='output-flood'),
    pytest.param('resource_limit', 'resource_limit', id='resource-limit'),
    pytest.param('failed', 'adapter_error', id='nonzero-exit'),
    pytest.param('permission_audit_unavailable', 'sandbox_failed', id='unknown-status-is-not-completed'),
])
def test_a_run_that_does_not_complete_is_a_failure_with_the_matching_code(status, code):
    result = run(failure_envelope(), FakeBackend(completed('', status=status, exit_code=1)))
    assert (result['outcome'], result['code'], result['presented'], result['transformation']) == ('failed', code, None, None)
    assert (result['worker']['status'], result['worker']['network']) == (status, 'none')


@pytest.mark.parametrize('reason', [
    pytest.param('namespace_probe_failed', id='namespaces-denied'),
    pytest.param('process_limit_exhausted', id='process-limit'),
    pytest.param('keyring_isolation_unavailable', id='no-keyring-isolation'),
])
def test_a_nonzero_exit_on_a_host_whose_sandbox_cannot_start_is_not_blamed_on_the_adapter(reason):
    backend = FakeBackend(completed('', status='failed', exit_code=1), capability=dict(status='unsupported', reason=reason))
    result = run(failure_envelope(), backend)
    assert (result['outcome'], result['code'], result['presented'], result['worker']) == (
        'failed', 'sandbox_unavailable', None, None)
    assert backend.probes == 1


@pytest.mark.parametrize('policy', [
    pytest.param('required', id='required'), pytest.param('optional-identity', id='optional-identity')])
def test_a_nonzero_exit_from_a_working_sandbox_is_an_adapter_error_and_a_completed_run_is_never_probed(policy):
    failing, finished = FakeBackend(completed('', status='failed', exit_code=1)), scripted(failure_envelope())
    assert run(failure_envelope(), failing, policy=policy)['code'] == 'adapter_error' and failing.probes == 1
    assert run(failure_envelope(), finished, policy=policy)['code'] is None and finished.probes == 0


@pytest.mark.parametrize(('error', 'code'), [
    pytest.param(TimeoutError('sandbox budget exhausted before launch'), 'timeout', id='budget-exhausted'),
    pytest.param(ValueError('bubblewrap is unavailable'), 'sandbox_unavailable', id='no-bubblewrap'),
    pytest.param(KeyringUnavailable('keyring'), 'sandbox_unavailable', id='no-keyring-isolation'),
    pytest.param(OSError('exec failed'), 'sandbox_unavailable', id='launch-failure'),
    pytest.param(subprocess.SubprocessError('spawn'), 'sandbox_unavailable', id='subprocess-error'),
])
def test_a_sandbox_that_cannot_start_is_a_failure_never_a_pass(error, code):
    result = run(failure_envelope(), FakeBackend(error=error))
    assert (result['outcome'], result['code'], result['presented'], result['worker']) == ('failed', code, None, None)


def test_an_answer_is_rejected_when_the_read_only_inputs_changed_during_the_run():
    envelope = failure_envelope()
    result = run(envelope, scripted(envelope, inputs_unchanged=False))
    assert (result['outcome'], result['code'], result['presented']) == ('failed', 'input_modified', None)


@pytest.mark.parametrize('stdout', [
    pytest.param('', id='empty'),
    pytest.param('opening log line', id='not-json'),
    pytest.param('{"original":', id='truncated'),
    pytest.param('{"original": NaN}', id='nan-constant'),
    pytest.param('Infinity', id='infinity-constant'),
    pytest.param('[' * 100000, id='unbounded-nesting'),
])
def test_unparseable_worker_output_is_a_malformed_output_failure(stdout):
    result = run(failure_envelope(), FakeBackend(completed(stdout)))
    assert (result['outcome'], result['code'], result['presented']) == ('failed', 'malformed_output', None)


@pytest.mark.parametrize('literal', [
    pytest.param('1e999', id='positive-overflow'),
    pytest.param('-1e999', id='negative-overflow'),
    pytest.param('1E+400', id='uppercase-exponent'),
    pytest.param('[0, 1e999]', id='inside-a-list'),
])
@pytest.mark.parametrize('policy', [
    pytest.param('required', id='required'), pytest.param('optional-identity', id='optional-identity')])
def test_a_numeric_literal_that_overflows_to_infinity_is_malformed_output_not_an_exception(literal, policy):
    envelope = failure_envelope(n='SENTINEL')
    stdout = json.dumps(answer(envelope), sort_keys=True).replace('"SENTINEL"', literal)
    result = run(envelope, FakeBackend(completed(stdout)), policy=policy)
    assert (result['code'], result['outcome']) == ('malformed_output', {'required': 'failed', 'optional-identity': 'fallback'}[policy])
    assert result['presented'] == (identity(envelope, AUTH, ADAPTER_ID) if policy == 'optional-identity' else None)


def test_a_large_but_finite_number_is_still_accepted():
    envelope = failure_envelope(n='SENTINEL')
    stdout = json.dumps(answer(envelope), sort_keys=True).replace('"SENTINEL"', '1e300')
    result = run(envelope, FakeBackend(completed(stdout)))
    assert result['code'] == 'contract_violation'  # parsed and compared: it differs from the original, not unparseable


def test_a_contract_violation_is_a_failure_and_the_altered_answer_is_not_returned():
    laundered = mutated(answer(failure_envelope()), lambda p: p['original'].update(status='success'))
    result = run(failure_envelope(), FakeBackend(completed(json.dumps(laundered))))
    assert (result['outcome'], result['code'], result['presented']) == ('failed', 'contract_violation', None)


def test_an_optional_policy_falls_back_to_the_identity_envelope_and_records_the_code():
    envelope = failure_envelope()
    result = run(envelope, FakeBackend(completed('opening log line')), policy='optional-identity')
    assert (result['outcome'], result['code'], result['transformation']) == ('fallback', 'malformed_output', 'identity')
    assert result['presented'] == identity(envelope, AUTH, ADAPTER_ID)
    assert 'error_metadata' not in result['presented'] and result['presented']['original']['status'] == 'error'


def test_a_required_policy_never_falls_back_to_identity():
    result = run(failure_envelope(), FakeBackend(completed('opening log line')), policy='required')
    assert (result['outcome'], result['presented']) == ('failed', None)


@pytest.mark.parametrize('policy', [
    pytest.param('required', id='required'), pytest.param('optional-identity', id='optional-identity')])
def test_an_input_above_the_declared_bound_is_a_code_and_the_adapter_never_runs(policy):
    envelope = failure_envelope(blob='x' * 5000)
    backend = scripted(envelope)
    result = run(envelope, backend, policy=policy)
    assert (result['code'], backend.calls) == ('oversized', [])
    assert result['outcome'] == {'required': 'failed', 'optional-identity': 'fallback'}[policy]
    assert result['presented'] == (identity(envelope, AUTH, ADAPTER_ID) if policy == 'optional-identity' else None)


@pytest.mark.parametrize('policy', [
    pytest.param('required', id='required'), pytest.param('optional-identity', id='optional-identity')])
def test_a_tool_result_beyond_the_shared_envelope_bound_is_a_code_not_an_exception(policy):
    envelope = success_envelope()
    envelope['payload']['content'] = 'x' * 70000
    backend = FakeBackend(completed('{}'))
    result = run(envelope, backend, policy=policy, max_input_bytes=65536)
    assert (result['code'], backend.calls) == ('oversized', [])
    assert result['outcome'] == {'required': 'failed', 'optional-identity': 'fallback'}[policy]
    assert result['presented'] is None and result['call_id'] == 'toolu_2'
    assert result['original_sha256'] == payload_digest(envelope)


def test_a_failed_result_carries_no_worker_output_or_tool_text():
    noisy = completed('opening log line ' + INJECTION, status='failed', exit_code=1, stderr='trace ' + INJECTION)
    result = run(failure_envelope(), FakeBackend(noisy))
    assert INJECTION not in json.dumps(result) and 'stdout' not in result['worker'] and 'stderr' not in result['worker']
