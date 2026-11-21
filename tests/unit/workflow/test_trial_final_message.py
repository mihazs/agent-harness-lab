"""The refining proposer of an installing run sees what a failed trial agent said it did."""
import json

from agent_harness_lab.workflow import _final_message


def test_the_last_agent_message_is_read_from_claude_and_codex_traces_and_bounded():
    # Regression (fixed-in: 0.232.4): live refinements missed a reproduced weakness again and again; the proposer saw only
    # pass or fail, never what the trial agent did under its change.
    claude = '\n'.join(json.dumps(e) for e in ({'type': 'assistant'}, {'type': 'result', 'result': 'I branched from the open PR.'}))
    codex = '\n'.join(json.dumps(e) for e in ({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'first'}},
                                              {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'stacked it'}}))
    assert _final_message(claude) == 'I branched from the open PR.'
    assert _final_message(codex) == 'stacked it'
    assert _final_message(json.dumps({'type': 'result', 'result': 'x' * 5000})) == 'x' * 1500
    assert _final_message(None) is None and _final_message('not json\n[1]') is None
