"""Synthetic refinement must not report an earlier attempt's instruction disposition as current."""
import json
from pathlib import Path

from agent_harness_lab.workflow import improve
from tests.integration.workflow.test_workflow import FAKE
from tests.integration.workflow.test_workflow import wf as wf


def test_no_change_refinement_does_not_inherit_prior_instruction_disposition(wf):
    program = FAKE.replace("if mode=='no_change':", "if mode=='no_change' or (mode=='refine' and c['attempt']==1):")
    program = program.replace("mode in ('rooted','drift')", "mode in ('rooted','drift','refine')")
    program = program.replace("mode in ('rooted','drift','ambient')", "mode in ('rooted','drift','ambient','refine')")
    wf.agent.write_text(program); wf.agent.chmod(0o700); (wf.project/'mode').write_text('refine')
    result = improve(wf.args())
    assert result['status'] == 'no_change' and result['reason'] == 'no_change_evidence_insufficient'
    assert 'instruction_removal' not in result and 'instruction_validation' not in result
    saved = json.loads((Path(result['output'])/'workflow.json').read_text())
    assert 'instruction_removal' not in saved and 'instruction_validation' not in saved


def test_budget_exhaustion_does_not_inherit_prior_instruction_disposition(wf):
    program = FAKE.replace("mode in ('rooted','drift')", "mode in ('rooted','drift','refine')")
    program = program.replace("mode in ('rooted','drift','ambient')", "mode in ('rooted','drift','ambient','refine')")
    wf.agent.write_text(program); wf.agent.chmod(0o700); (wf.project/'mode').write_text('refine')
    args = wf.args(); args.trial_limit = 8
    result = improve(args)
    assert result['status'] == 'budget_exhausted'
    assert 'instruction_removal' not in result and 'instruction_validation' not in result
    saved = json.loads((Path(result['output'])/'workflow.json').read_text())
    assert 'instruction_removal' not in saved and 'instruction_validation' not in saved


def test_blocked_refinement_does_not_inherit_prior_removal_disposition(wf):
    program = FAKE.replace("mode in ('rooted','drift')", "mode in ('rooted','drift','refine')")
    program = program.replace("mode in ('rooted','drift','ambient')", "mode in ('rooted','drift','ambient','refine')")
    program = program.replace("   (evaluation/'proposal.json').write_text(json.dumps(proposal))",
        "   if mode=='refine' and c['attempt']==1: proposal['instruction_plan']={'schema_version':999}\n"
        "   (evaluation/'proposal.json').write_text(json.dumps(proposal))")
    wf.agent.write_text(program); wf.agent.chmod(0o700); (wf.project/'mode').write_text('refine')
    result = improve(wf.args())
    assert result['status'] == 'blocked' and result['reason'] == 'instruction_validation_blocked'
    assert 'instruction_removal' not in result
    assert result['instruction_validation'].endswith('instruction-validation-1.json')
    saved = json.loads((Path(result['output'])/'workflow.json').read_text())
    assert 'instruction_removal' not in saved and saved['instruction_validation'] == result['instruction_validation']
