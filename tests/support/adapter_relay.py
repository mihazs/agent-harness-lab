"""Synthetic hook events and scripted worker answers for the relay tests."""
import json

from agent_harness_lab.evaluation.adapters import normalize_error
from tests.support.adapter_worker import ADAPTER_ID, INJECTION, FakeBackend, authorization, completed

RUN = 'run-1'


def hook_event(name='PostToolUseFailure', call_id='toolu_1', tool='Grep', **fields):
    """A native hook event; the defaults match failure_envelope() and success_envelope() once the bridge wraps them."""
    data = dict(session_id=RUN, hook_event_name=name, tool_name=tool, tool_use_id=call_id, cwd='/work',
                tool_input={'pattern': 'x'}, duration_ms=3)
    if name == 'PostToolUse':
        data.update(tool_response=dict(content='hello'))
    else:
        data.update(error='Exit code 2\n' + INJECTION, is_interrupt=False)
    data.update(fields)
    return json.dumps(data).encode()


def answered(envelope, code):
    """The adapter answer for an error envelope, naming code as its category."""
    presented = normalize_error(envelope, authorization(), ADAPTER_ID)
    presented['error_metadata']['code'] = code
    return presented


def scripted(envelope, code='unknown'):
    return FakeBackend(completed(json.dumps(answered(envelope, code))))


class EchoBackend(FakeBackend):
    """Answers every call with the adapter answer for the envelope it was handed, naming the id's last part."""
    def run(self, argv, workspace, inputs, role, limits):
        envelope = json.loads((workspace / 'envelope.json').read_bytes())
        self.result = completed(json.dumps(answered(envelope, envelope['call_id'].rpartition('_')[2])))
        return super().run(argv, workspace, inputs, role, limits)
