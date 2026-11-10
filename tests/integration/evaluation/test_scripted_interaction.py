"""Exact synthetic fixtures exercise a live fact, not semantic classification."""
import hashlib
import json
from unittest.mock import Mock

import pytest

from agent_harness_lab.evaluation import interaction_evidence as evidence, scripted_interaction as scripted
from tests.integration.evaluation.test_interaction_transport_fact import run

pytest_plugins = ["tests.integration.evaluation.test_interaction_transport_fact"]
from tests.integration.sandbox import test_sandbox_inference as wire


def oracle(live, **changes):
    comp = live[3]
    task = comp.active['scheduled']['task']
    root = next(row[1] for row in comp.spec.tasks if row[0] == task)
    value = dict(profile=scripted.PROFILE, task_id=task, root_id=root,
                 alternatives=['What target?', 'Which target?'], reply='SYNTHETIC_HIDDEN_REPLY')
    value.update(changes)
    raw = json.dumps(value).encode()
    return raw, hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('text,expected', [
    ('What target?', 'SYNTHETIC_HIDDEN_REPLY'), ('Which target?', 'SYNTHETIC_HIDDEN_REPLY'),
    ('What target? extra', None), ('I completed the task.', None),
])
def test_exact_alternatives_only(live, text, expected):
    message=wire.message();message['content'][0]['text']=text
    live[2].transport=Mock(return_value=wire.reply([message]))
    run(live)
    assert scripted.reply(evidence.current_view(), *oracle(live)) == expected


def test_rejects_changed_or_cross_root_oracle(live):
    run(live);view=evidence.current_view();raw,pin=oracle(live)
    with pytest.raises(ValueError):scripted.reply(view,raw+b' ',pin)
    with pytest.raises(ValueError):scripted.reply(view,*oracle(live,root_id='other'))
    with pytest.raises(ValueError):scripted.reply(view,*oracle(live,task_id='other'))


def test_unavailable_live_view_cannot_release_reply(live):
    with pytest.raises(ValueError):scripted.reply(object(),*oracle(live))


def test_refusal_never_releases_reply(live):
    message=wire.message();message['content']=[{'type':'refusal','refusal':'What target?'}]
    live[2].transport=Mock(return_value=wire.reply([message]))
    run(live)
    assert scripted.reply(evidence.current_view(),*oracle(live)) is None


def test_reply_is_not_exposed_in_summary_or_mismatch(live):
    run(live);view=evidence.current_view()
    assert scripted.reply(view,*oracle(live)) is None
    assert 'SYNTHETIC_HIDDEN_REPLY' not in repr(evidence.summary(view))


def test_success_is_single_use_for_live_trial_even_with_another_oracle(live):
    message=wire.message();message['content'][0]['text']='What target?'
    live[2].transport=Mock(return_value=wire.reply([message]))
    run(live);view=evidence.current_view()
    assert scripted.reply(view,*oracle(live)) == 'SYNTHETIC_HIDDEN_REPLY'
    with pytest.raises(ValueError):
        scripted.reply(view,*oracle(live,reply='SYNTHETIC_OTHER_REPLY'))
