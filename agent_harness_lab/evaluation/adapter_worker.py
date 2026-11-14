"""Isolated presentation worker: runs one declared adapter's exact bytes in the offline sandbox and verifies the result.

present() hash-checks the adapter bytes, writes the already authorized tool envelope and the bytes to a private
directory, runs the controller-owned RUNNER in the offline bubblewrap sandbox (no network, host home, credentials or
oracle; bounded time, memory and output) and accepts the answer only when it keeps the original envelope exactly,
binds the adapter and authorization, and adds nothing but a bounded, separate error_metadata. A failure under the
'required' policy stops the call; under 'optional-identity' it falls back to the identity envelope and records the
code. An unavailable sandbox is a failure, never a pass. Schema validity is not semantic correctness, and the worker is
not connected to any hook: delivery of a non-identity result to the model is still blocked (see
docs/adapter-runtime-decision.md). The result is private provenance and never carries worker stdout or stderr.
"""
from pathlib import Path
import copy
import json
import subprocess
import tempfile

from .adapter_bridge import POLICIES, payload_digest
from .adapters import TRANSFORMS, AdapterSpec, Authorization, identity, verify_adapter_bytes
from .sandbox import Limits, SandboxBackend

WORKER_SCHEMA = 'ahl.adapter-worker-result.v1'
MEMORY_BYTES = 128 << 20
PROCESSES = 8
# Controller-owned: the adapter module is the only code the declared bytes contribute. -I drops the cwd and the
# environment from the import path, so nothing but /inputs/harness and the system directories can be imported.
RUNNER = ("import json,sys\nsys.path.insert(0,'/inputs/harness')\nimport adapter\n"
          "envelope=json.load(open('/work/envelope.json'))\n"
          "sys.stdout.write(json.dumps(adapter.present(envelope),sort_keys=True,allow_nan=False))\n")
RUN_FAILURES = {'timeout': 'timeout', 'output_limit': 'oversized', 'resource_limit': 'resource_limit', 'failed': 'adapter_error'}
ENVELOPE_KEYS = {'original', 'adapter_id', 'authorization_digest'}
ERROR_METADATA_KEYS = {'code', 'status', 'retry_authorized'}


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _refuse_constant(_):
    raise ValueError('non-finite JSON number')


def check_presented(original, presented, authorization, spec, transform):
    """None when presented keeps original exactly and adds only allowed metadata, else a failure code."""
    if not isinstance(presented, dict):
        return 'malformed_output'
    metadata_allowed = transform == 'structured-error-v1' and original['status'] == 'error'  # a success gets none
    if set(presented) != ENVELOPE_KEYS | ({'error_metadata'} if metadata_allowed else set()) \
            or presented['adapter_id'] != spec.adapter_id or presented['authorization_digest'] != authorization.digest \
            or payload_digest(presented['original']) != payload_digest(original):
        return 'contract_violation'
    if metadata_allowed:
        metadata = presented['error_metadata']
        if not (isinstance(metadata, dict) and set(metadata) == ERROR_METADATA_KEYS and metadata['status'] == 'error'
                and metadata['retry_authorized'] is False and isinstance(metadata['code'], str)
                and 0 < len(metadata['code']) <= 128):
            return 'contract_violation'
    return None


def _run(backend, adapter_bytes, original, limits):
    """(code, presented, worker) for one sandboxed run; presented is None whenever code is not None."""
    with tempfile.TemporaryDirectory(prefix='ahl-adapter-') as folder:
        root = Path(folder).resolve()
        harness, work = root / 'harness', root / 'work'
        harness.mkdir(), work.mkdir()
        (harness / 'adapter.py').write_bytes(adapter_bytes)
        (work / 'envelope.json').write_bytes(_encode(original))
        try:
            run = backend.run(['/usr/bin/python3', '-I', '-B', '-c', RUNNER], work, {'harness': harness}, 'executor', limits)
        except TimeoutError:
            return 'timeout', None, None
        except (OSError, ValueError, subprocess.SubprocessError):  # includes KeyringUnavailable and a missing bubblewrap
            return 'sandbox_unavailable', None, None
    attestation = run['attestation']
    worker = dict(status=run['status'], exit_code=run['exit_code'], backend=attestation['backend'],
                  role=attestation['role'], network=attestation['network'], resource_limits=attestation['resource_limits'])
    if run['status'] != 'completed':
        return RUN_FAILURES.get(run['status'], 'sandbox_failed'), None, worker
    if run['inputs_unchanged'] is not True:
        return 'input_modified', None, worker
    try:
        return None, json.loads(run['stdout'], parse_constant=_refuse_constant), worker
    except (ValueError, RecursionError):
        return 'malformed_output', None, worker


def present(authorization, spec, adapter_bytes, envelope, *, transform, policy='required', backend=None):
    """Run the declared adapter on one authorized tool envelope; returns a private result dict.

    outcome is 'applied' (presented passed check_presented), 'fallback' (optional-identity: presented is the identity
    envelope, or None when the original exceeds the shared 64 KiB envelope bound and the runtime keeps it, and code
    records why) or 'failed' (required: presented is None). Programmer errors raise; every event-level condition, an
    oversized input included, is a code."""
    if not isinstance(authorization, Authorization) or not isinstance(spec, AdapterSpec):
        raise TypeError('authorization and spec must be typed controller objects')
    if policy not in POLICIES or transform not in TRANSFORMS or transform not in spec.transformations:
        raise ValueError('policy and transform must be a frozen known policy and a declared transform')
    if spec.adapter_id not in authorization.adapter_ids or not set(spec.tools) <= set(authorization.tools):
        raise ValueError('adapter spec exceeds the project authorization')
    if not verify_adapter_bytes(spec, adapter_bytes):
        raise ValueError('worker needs the exact declared adapter bytes')
    try:
        original, beyond_bound = identity(envelope, authorization, spec.adapter_id)['original'], False
    except ValueError as error:  # the shared envelope check bounds a tool result at 64 KiB; a larger one is an event
        if 'exceeds bound' not in str(error):
            raise
        original, beyond_bound = copy.deepcopy(envelope), True
    if original['tool'] not in spec.tools:
        raise ValueError('tool is not declared by the adapter spec')
    declared = dict(spec.limits)
    presented = worker = None
    if beyond_bound or len(_encode(original)) > declared['max_input_bytes']:
        code = 'oversized'
    else:
        limits = Limits(seconds=declared['timeout_ms'] / 1000, memory_bytes=MEMORY_BYTES, processes=PROCESSES,
                        output_bytes=declared['max_output_bytes'])
        code, presented, worker = _run(backend or SandboxBackend(), adapter_bytes, original, limits)
        code = code or check_presented(original, presented, authorization, spec, transform)
    outcome = 'applied'
    if code:
        presented, outcome = None, 'failed'
        if policy == 'optional-identity':  # beyond the shared bound no identity envelope exists: the original is kept
            presented, outcome = None if beyond_bound else identity(original, authorization, spec.adapter_id), 'fallback'
    return dict(schema=WORKER_SCHEMA, outcome=outcome, code=code, requested=transform, policy=policy,
                transformation={'applied': transform, 'fallback': 'identity'}.get(outcome), adapter_id=spec.adapter_id,
                call_id=original['call_id'], original_sha256=payload_digest(original), presented=presented, worker=worker)
