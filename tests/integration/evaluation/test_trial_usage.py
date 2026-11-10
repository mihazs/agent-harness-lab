"""Live comparison request usage with synthetic local transport only."""
import json
import time

import pytest

from agent_harness_lab.evaluation import comparison_custody, responses, trial_usage
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.cli import evaluator


def comparison_fixture(tmp_path, transport):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    harness = tmp_path/'harness'; harness.mkdir(); (harness/'AGENTS.md').write_text('Synthetic instructions.\n')
    deadline = time.monotonic()+20
    harness_hash = evaluator().digest(harness)
    spec = ExperimentSpec('synthetic', (('a','a','family'),('b','b','family')), 1, 0, 4, 2,
                          .05,.1,.05,(('family',1.),),harness_hash,harness_hash,'oracle','runtime')
    settings = dict(model='synthetic',max_requests=4,max_tool_calls=2,max_output_tokens=100,
                    max_request_bytes=16384,max_response_bytes=16384)
    client = responses.Client(settings,None,deadline,transport=transport)
    bundle = comparison_custody.bundle_binding(harness,deadline)
    comparison = comparison_custody.Comparison(spec,{'a':digest(workspace),'b':digest(workspace)},client,deadline,
                                                bundles={'baseline':bundle,'candidate':bundle})
    return comparison,client,workspace,harness,deadline


def response(*args):
    return json.dumps(dict(status='completed',output=[dict(type='message',id='synthetic-message',role='assistant',
        status='completed',content=[dict(type='output_text',text='done')])],
        usage=dict(input_tokens=5,output_tokens=2,total_tokens=7,input_tokens_details=dict(cached_tokens=3)))).encode()


def execute(comparison,client,workspace,harness,deadline, *, trial_id=None):
    return responses.run_role(client,None,workspace,{'harness':harness},'executor','synthetic instructions',
                              'synthetic prompt',deadline,trial_id=trial_id or comparison.active['scheduled']['trial_id'])


def test_live_receipt_uses_admitted_trial_and_stages_before_callback(tmp_path):
    comparison,client,workspace,harness,deadline = comparison_fixture(tmp_path,response)
    callbacks = []
    def checkpoint(report):
        callbacks.append(trial_usage.current_trial_usage())
        client.response_protocol_validated = True
        trial_usage._confirm(comparison,client,object(),dict(input_tokens=0,output_tokens=0,total_tokens=0))
        if client._usage: client._usage[0]['input_tokens'] = 0
    client.record = checkpoint
    with comparison:
        comparison.before(comparison.expected[0],workspace,harness,client)
        execute(comparison,client,workspace,harness,deadline)
        receipt = trial_usage.current_trial_usage()
        assert receipt['scheduled_trial_sha256'] == comparison_custody.hashed(comparison.expected[0])
        assert receipt['requests'] == [dict(request_ordinal=1,usage=dict(input_tokens=5,output_tokens=2,
                                                                        total_tokens=7,cached_input_tokens=3))]
        assert all(row['usage'] is None for snapshot in callbacks for row in snapshot['requests'])
        assert receipt['source'] == 'provider_reported_unverified' and receipt['publication_allowed'] is False
        assert receipt['automatic_promotion'] is False and receipt['certification_authority'] is False
        receipt['requests'][0]['usage']['cached_input_tokens'] = 0
        assert trial_usage.current_trial_usage()['requests'][0]['usage']['cached_input_tokens'] == 3
        with pytest.raises(ValueError,match='private'): trial_usage.current_trial_usage(public=True)
    with pytest.raises(ValueError,match='live admitted'): trial_usage.current_trial_usage()


def test_failed_transport_and_forged_trial_id_never_produce_usage(tmp_path):
    def fail(*args): raise responses.InferenceError('synthetic failure')
    comparison,client,workspace,harness,deadline = comparison_fixture(tmp_path,fail)
    with comparison:
        comparison.before(comparison.expected[0],workspace,harness,client)
        with pytest.raises(responses.InferenceError): execute(comparison,client,workspace,harness,deadline)
        receipt = trial_usage.current_trial_usage()
        assert receipt['request_attempts'] == 1 and receipt['requests'][0]['usage'] is None
    other = tmp_path/'other'; other.mkdir()
    comparison,client,workspace,harness,deadline = comparison_fixture(other,response)
    with comparison:
        comparison.before(comparison.expected[0],workspace,harness,client)
        with pytest.raises(ValueError,match='admitted trial'):
            execute(comparison,client,workspace,harness,deadline,trial_id='forged')
        receipt = trial_usage.current_trial_usage()
        assert receipt['request_attempts'] == 1 and receipt['unobserved_attempts'] == 1 and receipt['requests'] == []


def test_new_comparison_with_reused_client_cannot_see_prior_trial_usage(tmp_path):
    comparison,client,workspace,harness,deadline = comparison_fixture(tmp_path,response)
    with comparison:
        comparison.before(comparison.expected[0],workspace,harness,client)
        execute(comparison,client,workspace,harness,deadline)
        assert trial_usage.current_trial_usage()['requests'][0]['usage']['input_tokens'] == 5
    later = comparison_custody.Comparison(comparison.spec,comparison.fixtures,client,deadline,bundles=comparison.bundles)
    with later:
        later.before(later.expected[0],workspace,harness,client)
        forged = dict(scheduled_trial_sha256=comparison_custody.hashed(later.expected[0]),trial_id=later.expected[0]['trial_id'])
        trial_usage._begin(later,client,forged,object())
        receipt = trial_usage.current_trial_usage()
        assert receipt['request_attempts'] == 0 and receipt['requests'] == []


def test_checkpoint_cannot_reenter_transport_before_custody_confirmation(tmp_path):
    calls = []
    def transport(*args): calls.append(1); return response(*args)
    comparison,client,workspace,harness,deadline = comparison_fixture(tmp_path,transport)
    denied = []
    def checkpoint(report):
        if not report['request_pending'] and report['provider_reported_usage']:
            with pytest.raises(ValueError,match='reentrant'):
                client.respond([],deadline)
            denied.append(True)
    client.record = checkpoint
    with comparison:
        comparison.before(comparison.expected[0],workspace,harness,client)
        execute(comparison,client,workspace,harness,deadline)
        receipt = trial_usage.current_trial_usage()
        assert denied and len(calls) == 1
        assert receipt['request_attempts'] == 1 and receipt['requests'][0]['usage']['total_tokens'] == 7
