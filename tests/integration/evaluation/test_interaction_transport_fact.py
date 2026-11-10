"""Synthetic live transport facts; no semantic or native-runtime claim."""
from contextlib import ExitStack
from copy import copy
import json
from unittest.mock import Mock

import pytest

from agent_harness_lab.cli import evaluator
from agent_harness_lab.evaluation import comparison_custody as custody, interaction_evidence as evidence, responses
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.plan import ExperimentSpec
from tests.integration.evaluation.test_controller_actions import ControllerActionTests
from tests.integration.sandbox import test_sandbox_inference as wire


@pytest.fixture
def live():
    with ExitStack() as cleanup:
        fixture=ControllerActionTests();fixture.setUp();cleanup.callback(fixture.close)
        workspace=fixture.root/'work';workspace.mkdir()
        harness=fixture.root/'harness';harness.mkdir();(harness/'AGENTS.md').write_text('synthetic')
        version=evaluator().digest(harness);bundle=custody.bundle_binding(harness,fixture.deadline)
        plan=ExperimentSpec('synthetic',(('a','a','family'),('b','b','family')),1,1,4,2,.05,.1,.05,
            (('family',1.),),version,version,'oracle','runtime')
        message=wire.message();message['content'][0]['text']='SYNTHETIC_PRIVATE_RESPONSE'
        client,_=fixture.client([[message]])
        comp=custody.Comparison(plan,{'a':digest(workspace),'b':digest(workspace)},client,fixture.deadline,
            bundles=dict(baseline=bundle,candidate=bundle))
        with comp:
            comp.before(comp.expected[0],workspace,harness,client)
            yield fixture,workspace,client,comp


def run(live):
    fixture,workspace,client,comp=live
    responses.run_role(client,fixture.backend(),workspace,{},'executor','synthetic instructions','synthetic task',
                       fixture.deadline,trial_id=comp.active['scheduled']['trial_id'])


def test_live_fact_ignores_fabricated_public_ledger_and_expires(live):
    _,_,client,comp=live;run(live)
    client.actions.add('assistant_emission',client.requests,content=client.actions.text('SYNTHETIC_FORGED',100),
        content_type=client.actions.text('output_text',100),protocol_validated=True,response_disposition='validated',content_supported=True)
    view=evidence.current_view();summary=evidence.summary(view);rows=evidence._grader_rows(view)
    assert summary['coverage']=='retained_assistant_text_complete' and summary['whole_task_completed'] is None
    assert summary['user_reply']=='unobserved' and summary['transient_effects']=='unknown'
    assert not summary['automatic_promotion'] and not summary['publication_allowed']
    assert 'SYNTHETIC_PRIVATE_RESPONSE' not in repr(summary) and 'SYNTHETIC_FORGED' not in repr(rows)
    assert rows[0]['parts'][0]['text']=='SYNTHETIC_PRIVATE_RESPONSE'
    with pytest.raises(ValueError):evidence.summary(copy(view))
    with pytest.raises(TypeError):json.dumps(view)
    comp.active=None
    with pytest.raises(ValueError):evidence.summary(view)


def test_public_ledger_without_transport_cannot_mint_fact(live):
    _,_,client,_=live
    client.actions.begin('executor','synthetic-trial')
    client.actions.add('assistant_emission',1,content=client.actions.text('SYNTHETIC_FORGED',100),
        content_type=client.actions.text('output_text',100),protocol_validated=True,response_disposition='validated',content_supported=True)
    with pytest.raises(ValueError):evidence.current_view()


@pytest.mark.parametrize('text',['SYNTHETIC_CONTROLLER_KEY','x'*9000])
def test_private_response_redaction_and_truncation_remain_unknown(live,text):
    _,_,client,_=live
    message=wire.message();message['content'][0]['text']=text
    client.transport=Mock(return_value=wire.reply([message]))
    run(live);view=evidence.current_view()
    assert evidence.summary(view)['coverage']=='unknown'
    with pytest.raises(ValueError):evidence._grader_rows(view)


def test_unvalidated_transport_cannot_mint_response_fact(live):
    _,_,client,_=live
    frame=json.loads(wire.reply([wire.message()]));frame['status']='incomplete'
    client.transport=Mock(return_value=json.dumps(frame).encode())
    with pytest.raises(responses.InferenceError):run(live)
    with pytest.raises(ValueError):evidence.current_view()


def test_unfinished_return_keeps_response_fact_unavailable(live, monkeypatch):
    monkeypatch.setattr(evidence, '_finalize', lambda client: None)
    run(live)
    with pytest.raises(ValueError):
        evidence.current_view()
