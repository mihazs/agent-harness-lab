"""Keep every literal exit reason documented and rendered without success claims."""
import ast
from pathlib import Path

import pytest

from agent_harness_lab.exit_guidance import REMEDIATION, STATUS_ACTION, summary

ROOT = Path(__file__).resolve().parents[3]


def test_literal_exit_reasons_and_preflight_returns_have_remediation():
    reasons = set()
    for name in ('agent_harness_lab/workflow.py', 'agent_harness_lab/one_call.py'):
        tree = ast.parse((ROOT / name).read_text())
        reasons.update(keyword.value.value for node in ast.walk(tree) if isinstance(node, ast.Call)
                       for keyword in node.keywords if keyword.arg == 'reason'
                       and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str))
        reasons.update(branch.value for node in ast.walk(tree) if isinstance(node, ast.Call)
                       for keyword in node.keywords if keyword.arg == 'reason' and isinstance(keyword.value, ast.IfExp)
                       for branch in (keyword.value.body, keyword.value.orelse)
                       if isinstance(branch, ast.Constant) and isinstance(branch.value, str))
        if name.endswith('one_call.py'):
            function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'preflight')
            reasons.update(value.value for node in ast.walk(function) if isinstance(node, ast.Return) and node.value is not None
                           for value in ast.walk(node.value) if isinstance(value, ast.Constant) and isinstance(value.value, str))
    assert reasons <= REMEDIATION.keys(), sorted(reasons - REMEDIATION.keys())


def test_propagated_configuration_and_inference_reasons_are_registered():
    tree = ast.parse((ROOT / 'agent_harness_lab/config.py').read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'evaluation_settings')
    reasons = {node.value.value for node in ast.walk(function) if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == 'reason' for target in node.targets)
               and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)}
    tree = ast.parse((ROOT / 'agent_harness_lab/evaluation/responses.py').read_text())
    reasons.update(node.args[0].value for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Name) and node.func.id == 'InferenceError' and node.args
                   and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str))
    assert reasons <= REMEDIATION.keys(), sorted(reasons - REMEDIATION.keys())


@pytest.mark.parametrize('reason', sorted(REMEDIATION))
def test_reason_has_one_documented_action_and_safe_summary(reason):
    action = REMEDIATION[reason]
    document = (ROOT / 'docs/analysis.md').read_text()
    assert document.count(f'| `{reason}` | {action} |') == 1
    rendered = summary({'status': 'inconclusive', 'reason': reason})
    assert rendered.endswith('Next: ' + action)
    assert 'capture completeness remains unverified' in rendered
    assert 'no effect' not in rendered.lower()


@pytest.mark.parametrize('status', sorted(STATUS_ACTION))
def test_status_without_reason_has_one_documented_action(status):
    action = STATUS_ACTION[status]
    assert (ROOT / 'docs/analysis.md').read_text().count(f'| `status:{status}` | {action} |') == 1
    assert summary({'status': status}).endswith('Next: ' + action)


def test_empty_observed_blocked_and_unknown_summaries():
    empty = summary({'status': 'analyzed', 'analysis': {'evidence_state': 'empty', 'events': 0, 'cases': 0}})
    assert empty.startswith('No data.') and '0 events, 0 cases' in empty and 'ahl audit' in empty
    assert 'model costs' in empty
    assert summary({'status': 'no_change', 'reason': 'no_change_harness_supported'}).startswith('No change proposed.')
    insufficient = summary({'status': 'no_change', 'reason': 'no_change_evidence_insufficient',
                            'next_steps': [dict(rank=1, action='Capture outcomes.', command='ahl evaluate --help')]})
    assert insufficient.startswith('Evidence insufficient.') and 'ahl evaluate --help' in insufficient
    assert 'harness is fine' not in insufficient.replace('does not show the harness is fine', '')
    assert summary({'status': 'blocked', 'reason': 'agent_runtime_unavailable'}).startswith('Blocked by missing prerequisite.')
    assert summary({'status': 'inconclusive', 'reason': 'unknown_future_reason'}).startswith('Inconclusive.')
    assert summary({'status': 'no_change', 'reason': 'unknown_future_reason'}).startswith('Inconclusive.')
    assert summary({'status': 'analyzed', 'analysis': {'evidence_state': 'unknown'}}).startswith('Inconclusive.')
    assert summary({'status': 'analyzed', 'analysis': {'evidence_state': 'unknown'}}).endswith('Review the private receipt before retrying.')
    assert set(STATUS_ACTION) >= {'no_change', 'blocked', 'inconclusive'}
