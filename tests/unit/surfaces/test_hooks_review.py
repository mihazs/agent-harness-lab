"""hook_review_v1 packets and their hook-review.json file; synthetic projects only, nothing is executed."""
import json
from pathlib import Path

import pytest

from agent_harness_lab import hooks_review, hooks_validation
from agent_harness_lab.hooks_contract import evaluate, snapshot_record, stage

SETTINGS = b'{\n  "permissions": {"allow": ["Bash(ls)"]}\n}\n'
SCRIPT = '.claude/hooks/check.sh'
SCRIPT_BYTES = (b'#!/bin/sh\nexit 0\n', 0o755)


def entry(matcher='Bash', script=SCRIPT):
    return {'matcher': matcher, 'hooks': [dict(type='command', command=f'"$CLAUDE_PROJECT_DIR"/{script}', timeout=30)]}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    (root / '.claude').mkdir(parents=True)
    (root / '.claude/settings.json').write_bytes(SETTINGS)
    return root


def unit(project, value, scripts=None):
    before, after = stage(project, '.claude/settings.json', value, {SCRIPT: SCRIPT_BYTES} if scripts is None else scripts)
    return before, after, evaluate(project, before, after)


def test_packet_states_the_semantic_delta_and_leaves_unmeasured_facts_unmeasured(project):
    before, after, contract = unit(project, {'PreToolUse': [entry()]})
    packet = hooks_review.review_packet(contract, before, after, expected_effect='  ')
    assert packet['config_delta']['added'] == [dict(event='PreToolUse', matcher='Bash', handler=entry()['hooks'][0])]
    assert packet['config_delta']['removed'] == [] and packet['config_delta']['order_preserved']
    assert [(row['path'], row['change']) for row in packet['scripts']] == [(SCRIPT, 'added')]
    assert packet['unit_sha256'] == contract.sha256 and SCRIPT in packet['patch']
    assert packet['expected_effect'] == 'not_stated'
    assert packet['capability_receipt']['contract_status'] == 'passed'
    assert packet['evaluation'] == dict(
        disposition='not_validated', effective=False, improvement_eligible=False, disqualifiers=[],
        runtime_kind='none', task_effect='not_evaluated', instruction_only_alternative='not_compared',
        inherited_hooks='unknown')
    assert packet['overhead'] == dict(canary_run_seconds=None, scope='whole_canary_run_not_per_hook')
    assert packet['apply']['outcome_if_applied'] == 'applied_unverified'
    assert not packet['adoption_authority'] and not packet['publication_allowed']


def test_update_reports_added_and_removed_entries_and_a_changed_order(project):
    (project / SCRIPT).parent.mkdir()
    (project / SCRIPT).write_bytes(SCRIPT_BYTES[0]); (project / SCRIPT).chmod(0o755)
    first, second = entry('Bash'), entry('Write')
    base = json.dumps(dict(json.loads(SETTINGS), hooks={'PreToolUse': [first, second]}), indent=2).encode() + b'\n'
    (project / '.claude/settings.json').write_bytes(base)
    before, after, contract = unit(project, {'PreToolUse': [second, entry('Edit')]}, scripts={})
    delta = hooks_review.review_packet(contract, before, after)['config_delta']
    assert [row['matcher'] for row in delta['added']] == ['Edit']
    assert [row['matcher'] for row in delta['removed']] == ['Bash']
    assert delta['order_preserved']
    before, after, contract = unit(project, {'PreToolUse': [second, first]}, scripts={})
    assert not hooks_review.review_packet(contract, before, after)['config_delta']['order_preserved']


def test_a_blocked_unit_never_claims_untouched_settings(project):
    before, after, contract = unit(project, {'PreToolUse': [entry(script='.claude/hooks/missing.sh')]}, scripts={})
    assert contract.status == 'blocked'
    assert hooks_review.review_packet(contract, before, after)['config_delta']['other_settings'] == 'unverified'


def test_validation_carries_disposition_and_canary_time_into_the_packet(project):
    before, after, contract = unit(project, {'PreToolUse': [entry()]})
    document = dict(candidate=dict(status='effective', effective=True, improvement_eligible=False,
                                   disqualifiers=['hides_errors']),
                    run=dict(elapsed_seconds=1.5), runtime_kind='fake',
                    inherited_hooks='managed_and_system_layers_unverified')
    packet = hooks_review.review_packet(contract, before, after, document, expected_effect='stops a bad edit')
    assert packet['evaluation']['disposition'] == 'effective' and packet['evaluation']['disqualifiers'] == ['hides_errors']
    assert packet['evaluation']['task_effect'] == 'not_evaluated'
    assert packet['overhead']['canary_run_seconds'] == 1.5 and packet['expected_effect'] == 'stops a bad edit'
    assert len(packet['evidence_sha256']) == 64


def test_only_hook_units_are_reviewed(project):
    before, after = stage(project, '.claude/settings.json', {}, {})
    contract = evaluate(project, before, after)
    object.__setattr__(contract, 'profile', 'workflow_contract_v1')
    with pytest.raises(ValueError):
        hooks_review.review_packet(contract, before, after)


def test_validate_writes_a_private_review_packet_for_a_staged_unit_and_none_without_one(project, tmp_path):
    before, after, contract = unit(project, {'PreToolUse': [entry()]})
    root, document = hooks_validation.validate(
        tmp_path / 'out', 'claude-code', None, unit=('.claude/settings.json', before, after), project=project)
    path = root / hooks_validation.REVIEW
    packet = json.loads(path.read_text())
    assert path.stat().st_mode & 0o777 == 0o600
    assert packet['unit_sha256'] == contract.sha256 and packet['evaluation']['disposition'] == document['candidate']['status']
    assert document['executed'] is False and not Path(project / SCRIPT).exists()
    root, _ = hooks_validation.validate(tmp_path / 'canary', 'claude-code', None)
    assert not (root / hooks_validation.REVIEW).exists()


def test_duplicate_entries_are_counted_not_collapsed(project):
    (project / SCRIPT).parent.mkdir()
    (project / SCRIPT).write_bytes(SCRIPT_BYTES[0]); (project / SCRIPT).chmod(0o755)
    base = json.dumps(dict(json.loads(SETTINGS), hooks={'PreToolUse': [entry(), entry()]}), indent=2).encode() + b'\n'
    (project / '.claude/settings.json').write_bytes(base)
    before, after, contract = unit(project, {'PreToolUse': [entry()]}, scripts={})
    delta = hooks_review.review_packet(contract, before, after)['config_delta']
    assert delta['added'] == [] and len(delta['removed']) == 1 and delta['order_preserved']
    before, after, contract = unit(project, {'PreToolUse': [entry(), entry(), entry()]}, scripts={})
    delta = hooks_review.review_packet(contract, before, after)['config_delta']
    assert len(delta['added']) == 1 and delta['removed'] == [] and delta['order_preserved']


def test_a_toml_datetime_handler_value_does_not_crash_the_packet(project):
    config = '.codex/config.toml'
    toml = b'[[hooks.PreToolUse]]\nmatcher = "Bash"\n[[hooks.PreToolUse.hooks]]\ntype = "command"\ncommand = "echo"\ntimeout = 1979-05-27T07:32:00Z\n'
    (project / '.codex').mkdir()
    before, after = {config: None}, {config: snapshot_record(toml, 0o644)}
    contract = evaluate(project, before, after)
    packet = hooks_review.review_packet(contract, before, after)
    assert [row['matcher'] for row in packet['config_delta']['added']] == ['Bash']
    assert '1979' in packet['config_delta']['added'][0]['handler']['timeout']


@pytest.mark.parametrize('runtime, value, scripts', [
    ('claude-code', {'PreToolUse': [entry(script='.claude/hooks/missing.sh')]}, {}),
    ('codex', {'PreToolUse': [entry()]}, None)])
def test_validate_writes_the_packet_for_an_unsupported_unit(project, tmp_path, runtime, value, scripts):
    before, after, _ = unit(project, value, scripts)
    root, document = hooks_validation.validate(
        tmp_path / 'out', runtime, None, unit=('.claude/settings.json', before, after), project=project)
    packet = json.loads((root / hooks_validation.REVIEW).read_text())
    assert packet['evaluation']['disposition'] == document['candidate']['status'] == 'unsupported'
    assert packet['overhead']['canary_run_seconds'] is None
