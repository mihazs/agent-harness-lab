"""Controller-owned relay from the supported hook to the isolated presentation worker; it installs no hook.

Relay wraps one Bridge, which keeps the run and call identity, ordering, duplicate, cancellation and stop semantics
of the identity bridge, and adds the non-identity step. Only a failed tool call (PostToolUseFailure) is presented, and
only as additive context: the runtime cannot replace a failed result, so the original output always reaches the model
unchanged. The context is controller text with one word of vocabulary, a category the project declared before the run;
whatever the adapter returns outside that vocabulary, and every byte of the tool result, stays out of the reply and
becomes 'unknown'. A successful call is answered with the empty identity reply without starting the worker. The worker
runs the declared bytes in the offline sandbox (see adapter_worker); a failure under 'required' stops the affected
execution, under 'optional-identity' it keeps the original output and is recorded. The ledger is metadata only, holds the
digest of every reply for a later model-visible observation, and is private. A fake runtime exercising the relay is never
evidence that the supported runtime applied it: the live run and the model-visible comparison are still open.
"""
import copy
import hashlib
import re

from .adapter_bridge import Bridge, _canonical
from .adapter_worker import present
from .adapters import TRANSFORMS

CATEGORY = re.compile(r'[A-Za-z0-9_.-]{1,64}')
UNKNOWN = 'unknown'
NOTE = 'ahl-presentation (controller text, not tool output): status=error category=%s retry_authorized=false'


def _reply(category):
    return dict(hookSpecificOutput=dict(hookEventName='PostToolUseFailure', additionalContext=NOTE % category))


class Relay:
    """One run's relay. Construction rejects what Bridge rejects, an undeclared transform and an unbounded vocabulary."""

    def __init__(self, authorization, spec, adapter_bytes, run_id, runtime_version, *, transform='structured-error-v1',
                 categories=(), policy='required', backend=None):
        self._bridge = Bridge(authorization, spec, adapter_bytes, run_id, runtime_version, policy)  # types the arguments
        if transform not in TRANSFORMS or transform not in spec.transformations:
            raise ValueError('relay needs a transform the adapter spec declares')
        if not isinstance(categories, tuple) or any(not isinstance(c, str) or not CATEGORY.fullmatch(c) for c in categories):
            raise ValueError('categories must be a tuple of bounded identifiers declared before the run')
        self._worker = dict(authorization=authorization, spec=spec, adapter_bytes=adapter_bytes, transform=transform,
                            policy=policy, backend=backend)
        self._categories = frozenset(categories) | {UNKNOWN}
        self._ledger, self._delivered, self._results = [], {}, {}

    def cancel(self, call_id):
        """Drop a late result for a call the controller cancelled; a delivered result is never recalled."""
        return self._bridge.cancel(call_id)

    def handle(self, raw):
        """Hook stdin bytes to hook stdout bytes. Never raises for a bad event and never alters tool effects."""
        reply = self._bridge.handle(raw)
        entry = self._bridge.last_entry()
        call_id = entry['call_id']
        delivered = dict(outcome=entry['outcome'], code=entry['code'], transformation=None, worker=None, reply=reply)
        if entry['outcome'] == 'duplicate':  # the same bytes, so the worker is not run again
            delivered = dict(self._delivered[call_id], outcome='duplicate', code=None)
        elif entry['outcome'] == 'applied':
            delivered = self._delivered[call_id] = self._present(call_id, entry['status'], reply)
        self._ledger.append(dict(seq=len(self._ledger) + 1, call_id=call_id, tool=entry['tool'], status=entry['status'],
                                 outcome=delivered['outcome'], code=delivered['code'],
                                 transformation=delivered['transformation'], original_sha256=entry['payload_sha256'],
                                 worker=delivered['worker'], reply_sha256=hashlib.sha256(delivered['reply']).hexdigest()))
        return delivered['reply']

    def _present(self, call_id, status, identity_reply):
        """What one call the bridge accepted is answered with; a success is never sent to the worker."""
        if status != 'error' or self._worker['transform'] == 'identity':
            return dict(outcome='identity', code=None, transformation='identity', worker=None, reply=identity_reply)
        result = present(**self._worker, envelope=self._bridge.provenance(call_id)['original'])
        self._results[call_id] = result
        worker = result['worker'] and {k: result['worker'][k] for k in ('status', 'backend', 'network')}
        if result['outcome'] == 'applied':
            category = result['presented']['error_metadata']['code']
            reply = _canonical(_reply(category if category in self._categories else UNKNOWN))
            return dict(outcome='presented', code=None, transformation=self._worker['transform'], worker=worker, reply=reply)
        if result['outcome'] == 'fallback':
            return dict(outcome='fallback', code=result['code'], transformation='identity', worker=worker,
                        reply=identity_reply)
        reply = _canonical({'continue': False, 'stopReason': 'adapter worker failed: ' + result['code']})
        return dict(outcome='failed', code=result['code'], transformation=None, worker=worker, reply=reply)

    def ledger(self):
        """Metadata-only entries in arrival order; private, and never a raw payload or worker output."""
        return copy.deepcopy(self._ledger)

    def result(self, call_id):
        """The private worker result of one presented call, or None."""
        return copy.deepcopy(self._results.get(call_id))
