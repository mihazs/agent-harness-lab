"""Projection of actual synthetic controller events, without payload retention."""
from agent_harness_lab.evaluation.history_report import action_timeline
from agent_harness_lab.evaluation.responses import InferenceError
from tests.integration.evaluation.test_controller_actions import ControllerActionTests
from tests.integration.sandbox import test_sandbox_inference as wire


def test_controller_generated_accepted_and_denied_actions_are_projected():
    fixture=ControllerActionTests();fixture.setUp()
    try:
        client,_=fixture.client([[wire.call()],[wire.message()]])
        fixture.role(client,fixture.backend('SYNTHETIC_SECRET'))
        timeline=action_timeline(client.action_report(),'synthetic-trial')
        tool=next(row for row in timeline['events'] if row['kind']=='tool_request')
        assert tool['decision']=='accepted' and tool['worker_outcome']=='observed'
        assert 'SYNTHETIC_SECRET' not in repr(timeline)
        client,_=fixture.client([[wire.call(name='undeclared')]])
        try:fixture.role(client,fixture.backend())
        except InferenceError:pass
        else:raise AssertionError('undeclared tool must be denied')
        timeline=action_timeline(client.action_report(),'synthetic-trial')
        assert next(row for row in timeline['events'] if row['kind']=='tool_request')['decision']=='denied'
    finally:
        fixture.doCleanups()
