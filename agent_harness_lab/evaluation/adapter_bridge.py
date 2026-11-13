"""Identity-only JSON-envelope bridge for one declared runtime hook; it loads and executes no adapter code.

probe_runtime() decides offline whether a runtime/version has a documented insertion point (it never starts or
contacts a runtime and never reports more than 'declared'). Bridge turns each native Claude Code PostToolUse or
PostToolUseFailure event into a tool-envelope-v1, binds it to the run and call identity, applies the identity
transform, verifies the result against the original and answers with an empty hook response, so the runtime
keeps the original output. The ledger holds metadata only; raw envelopes stay in the separate private provenance.
A required-policy failure stops the affected execution with continue=false; an optional-identity failure is
recorded and falls back to identity. capability_receipt() states what was applied and observed; absent evidence
stays unknown and a fake runtime is never counted as supported-runtime evidence. See docs/adapter-runtime-decision.md.
"""
import copy
import hashlib
import json
from pathlib import Path

from ..surfaces import PINS
from .adapters import AdapterSpec, Authorization, identity, runtime_capability, verify_adapter_bytes

PROBE_SCHEMA = 'ahl.adapter-runtime-probe.v1'
RECEIPT_SCHEMA = 'ahl.adapter-capability-receipt.v1'
EVENTS = {'PostToolUse': 'success', 'PostToolUseFailure': 'error'}
POLICIES = ('required', 'optional-identity')
OBSERVATION_SOURCES = ('fake-runtime',)
MAX_EVENT_BYTES = 1 << 20  # native event wrapper; the envelope itself is bounded by the spec's max_input_bytes
MAX_CALLS = 1024           # per run; the private provenance is bounded by calls times the spec limits


class BridgeFailure(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def payload_digest(value):
    """SHA-256 of a JSON value's canonical form; the runtime-side observer uses it for model-visible results."""
    return hashlib.sha256(_canonical(value)).hexdigest()


def _bridge_digest():
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        return None  # an unreadable bridge is unknown, and verify_receipt never accepts unknown


def _bounded(value):
    return isinstance(value, str) and 0 < len(value) <= 128


def probe_runtime(runtime, version):
    """Offline decision about one runtime/version; 'declared' is a documented insertion point, never proof."""
    candidate = runtime_capability(runtime, version).get('candidate')
    reasons = ['no_documented_insertion_point'] if not candidate else (
        [] if candidate['version_documented'] and version == PINS.get(runtime) else ['version_not_pinned'])
    return dict(schema=PROBE_SCHEMA, runtime=runtime, version=version, status='unsupported' if reasons else 'declared',
                reasons=reasons, verified=False, automatic_promotion=False,
                insertion_point=None if reasons else candidate['insertion_point'])


def _envelope(event):
    status = EVENTS.get(event.get('hook_event_name')) if isinstance(event, dict) else None
    if status is None:
        raise BridgeFailure('unsupported_event')
    if not _bounded(event.get('tool_use_id')) or not _bounded(event.get('tool_name')):
        raise BridgeFailure('malformed_event')
    if status == 'success' and 'tool_response' in event:
        payload = event['tool_response']
    elif status == 'error' and isinstance(event.get('error'), str) and isinstance(event.get('is_interrupt', False), bool):
        payload = dict(error=event['error'], is_interrupt=event.get('is_interrupt', False))
    else:
        raise BridgeFailure('malformed_event')
    return dict(call_id=event['tool_use_id'], tool=event['tool_name'], status=status, payload=payload)


class Bridge:
    """One run's bridge. Construction rejects an unsupported runtime/version, unauthorized spec or altered bytes."""

    def __init__(self, authorization, spec, adapter_bytes, run_id, runtime_version, policy='required'):
        if not isinstance(authorization, Authorization) or not isinstance(spec, AdapterSpec):
            raise TypeError('authorization and spec must be typed controller objects')
        probe = probe_runtime(spec.runtime, runtime_version)
        if probe['status'] != 'declared' or spec.runtime_version != runtime_version:
            raise ValueError('unsupported runtime integration: ' + ', '.join(probe['reasons'] or ['spec_runtime_version']))
        if spec.adapter_id not in authorization.adapter_ids or not set(spec.tools) <= set(authorization.tools):
            raise ValueError('adapter spec exceeds the project authorization')
        if 'identity' not in spec.transformations or not verify_adapter_bytes(spec, adapter_bytes):
            raise ValueError('bridge needs the declared identity transform and the exact declared adapter bytes')
        if not _bounded(run_id) or policy not in POLICIES:
            raise ValueError('run identity must be bounded text and policy a frozen known policy')
        self.authorization, self.spec, self.run_id, self.policy = authorization, spec, run_id, policy
        self.runtime_version = runtime_version
        self._limits = dict(spec.limits)
        # shortcut: the metadata ledger is unbounded across failed events; cap it if an optional bridge outlives one run
        self._ledger, self._provenance, self._cancelled = [], {}, set()

    def cancel(self, call_id):
        """Drop a late result for a call the controller cancelled; a delivered result is never recalled."""
        if not _bounded(call_id) or call_id in self._provenance:
            return False
        self._cancelled.add(call_id)
        return True

    def handle(self, raw):
        """Hook stdin bytes to hook stdout bytes. Never raises for a bad event and never alters tool effects."""
        envelope = digest = None
        try:
            envelope = self._parse(raw)
            digest = payload_digest(envelope['payload'])
            outcome = self._apply(envelope, digest)
            code, response = None, {}
        except (BridgeFailure, RecursionError) as failure:
            outcome, code = 'failed', getattr(failure, 'code', 'malformed_event')
            response = {} if self.policy == 'optional-identity' else {
                'continue': False, 'stopReason': 'adapter bridge failed: ' + code}
        entry = dict(seq=len(self._ledger) + 1, outcome=outcome, code=code, transformation='identity',
                     fallback=(('identity' if self.policy == 'optional-identity' else 'stop') if code else None),
                     call_id=envelope and envelope['call_id'], tool=envelope and envelope['tool'],
                     status=envelope and envelope['status'], payload_sha256=digest)
        self._ledger.append(entry)
        return _canonical(response)

    def _parse(self, raw):
        if not isinstance(raw, bytes):
            raise BridgeFailure('malformed_event')
        if len(raw) > MAX_EVENT_BYTES:
            raise BridgeFailure('oversized')
        try:
            event = json.loads(raw)
            envelope = _envelope(event)
            size = len(_canonical(envelope))
        except (ValueError, RecursionError):
            raise BridgeFailure('malformed_event') from None
        if event.get('session_id') != self.run_id:
            raise BridgeFailure('run_mismatch')
        if envelope['tool'] not in self.spec.tools:
            raise BridgeFailure('unauthorized_tool')
        if size > self._limits['max_input_bytes']:
            raise BridgeFailure('oversized')
        return envelope

    def _apply(self, envelope, digest):
        call_id = envelope['call_id']
        if call_id in self._cancelled:
            return 'cancelled'
        known = self._provenance.get(call_id)
        if known:
            if payload_digest(known['original']) != payload_digest(envelope):
                raise BridgeFailure('duplicate_conflict')
            return 'duplicate'
        if len(self._provenance) >= MAX_CALLS:
            raise BridgeFailure('call_limit')
        try:
            presented = identity(envelope, self.authorization, self.spec.adapter_id)
        except ValueError as error:  # the shared envelope check has its own, byte-count, bound
            raise BridgeFailure('oversized' if 'bound' in str(error) else 'verification_failed') from None
        if presented['original'] != envelope or set(presented) != {'original', 'adapter_id', 'authorization_digest'}:
            raise BridgeFailure('verification_failed')
        if len(_canonical(presented)) > self._limits['max_output_bytes']:
            raise BridgeFailure('oversized')
        self._provenance[call_id] = dict(original=copy.deepcopy(envelope), presented=presented)
        return 'applied'

    def ledger(self):
        """Metadata-only entries in arrival order; private, and never a raw payload."""
        return copy.deepcopy(self._ledger)

    def provenance(self, call_id):
        """The private original and presented envelopes of one applied call, or None."""
        return copy.deepcopy(self._provenance.get(call_id))

    def capability_receipt(self, observations=None, source=None):
        """State what was applied. observations maps call_id to payload_digest() of the model-visible result."""
        counts = {}
        for entry in self._ledger:
            counts[entry['outcome']] = counts.get(entry['outcome'], 0) + 1
        application = dict(state='unknown', source=None, observed=0, mismatched=[])
        if observations is not None:
            if source not in OBSERVATION_SOURCES:
                raise ValueError('observation source is not supported')
            expected = {e['call_id']: e['payload_sha256'] for e in self._ledger if e['outcome'] == 'applied'}
            seen = {c: expected[c] == observations.get(c) for c in expected if c in observations}
            mismatched = sorted(c for c, ok in seen.items() if not ok)
            state = 'mismatch' if mismatched else 'observed' if expected and len(seen) == len(expected) \
                else 'partial' if seen else 'unknown'
            application = dict(state=state, source=source, observed=len(seen), mismatched=mismatched)
        body = dict(schema=RECEIPT_SCHEMA, runtime=self.spec.runtime, runtime_version=self.runtime_version,
                    probe=probe_runtime(self.spec.runtime, self.runtime_version), tools=sorted(self.spec.tools),
                    transformation='identity', policy=self.policy, run_id=self.run_id, calls=counts,
                    bridge_sha256=_bridge_digest(), adapter_content_sha256=self.spec.content_digest,
                    spec_digest=self.spec.digest, authorization_digest=self.authorization.digest,
                    application=application, runtime_verified=False,
                    evidence_level='synthetic' if application['source'] else 'none')
        return dict(body, digest=hashlib.sha256(_canonical(body)).hexdigest())


def verify_receipt(receipt, authorization, spec, adapter_bytes):
    """True only when the receipt is untampered and still bound to the current authorization, spec and bytes."""
    try:
        body = {k: v for k, v in receipt.items() if k != 'digest'}
        current = _bridge_digest()
        return (hashlib.sha256(_canonical(body)).hexdigest() == receipt['digest'] and current is not None
                and body['bridge_sha256'] == current and body['spec_digest'] == spec.digest
                and body['authorization_digest'] == authorization.digest
                and body['adapter_content_sha256'] == spec.content_digest
                and verify_adapter_bytes(spec, adapter_bytes) and body['application']['state'] != 'mismatch'
                and body['probe']['status'] == 'declared')
    except (AttributeError, KeyError, TypeError, ValueError):
        return False
