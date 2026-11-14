"""A bank task stores the identity of its interaction contract and scripted-reply oracle."""
import hashlib
import json

import pytest

from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.interaction_contract import contract_sha256
from agent_harness_lab.evaluation.task_bank import TaskSpec, inspect, validate
from tests.support.area_bank import two_area_bank

TASK = 'localization'
ROOT = 'calculator'
REPLY = 'SYNTHETIC_HIDDEN_REPLY'
CONTRACT = dict(schema_version=1, profile='interaction-contract-v1', task_id=TASK, endpoint='whole_task',
                required_clarifications=['target'], allowed_clarifications=['target'],
                permitted_mutations=['app/calc.py'], protected_paths=[],
                stop_reason=None, completion=dict(kind='interaction'))
ORACLE = dict(profile='exact-scripted-interaction-v1', task_id=TASK, root_id=ROOT,
              alternatives=['Which target?'], reply=REPLY)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def bank_with(tmp_path, *, contract=CONTRACT, oracle=ORACLE, declare=('contract', 'oracle'), edit=None):
    """Copy the starter bank and bind the localization task to a synthetic contract and oracle."""
    path = two_area_bank(tmp_path, tags={})
    bundle = path.parent / 'graders' / TASK
    raw = {}
    for name, value in (('interaction-contract.json', contract), ('interaction-oracle.json', oracle)):
        if value is not None:
            raw[name] = json.dumps(value).encode()
            (bundle / name).write_bytes(raw[name])
    bank = json.loads(path.read_text())
    task = next(t for t in bank['tasks'] if t['task_id'] == TASK)
    if 'contract' in declare:
        task['interaction_contract_sha256'] = contract_sha256(contract)
    if 'oracle' in declare:
        task['interaction_oracle_sha256'] = sha(raw['interaction-oracle.json'])
    if edit:
        edit(path, task)
    task['grader_digest'] = digest(bundle)
    path.write_text(json.dumps(bank))
    return path


def redigest(path, key='grader_digest', folder='graders'):
    """Re-freeze a bundle or fixture after a test edits it, so only the named check can fail."""
    bank = json.loads(path.read_text())
    next(t for t in bank['tasks'] if t['task_id'] == TASK)[key] = digest(path.parent / folder / TASK)
    path.write_text(json.dumps(bank))


def test_task_without_interaction_fields_keeps_its_shape(tmp_path):
    path = two_area_bank(tmp_path, tags={})
    task = TaskSpec.parse(json.loads(path.read_text())['tasks'][0], analysis_only=True)
    assert (task.interaction_contract_sha256, task.interaction_oracle_sha256) == ('', '')
    assert 'interaction_contract_sha256' not in task.declaration()
    assert 'interaction' not in validate(path, verify_contents=False)


def test_declared_contract_and_oracle_validate_and_are_counted(tmp_path):
    path = bank_with(tmp_path)
    expected = dict(contracts=1, oracles=1)
    assert validate(path)['interaction'] == expected
    assert inspect(path)['interaction'] == expected


def test_contract_without_oracle_is_a_valid_declaration(tmp_path):
    path = bank_with(tmp_path, oracle=None, declare=('contract',))
    assert validate(path)['interaction'] == dict(contracts=1, oracles=0)


def test_declaration_round_trips_both_identities(tmp_path):
    bank = json.loads(bank_with(tmp_path).read_text())
    task = TaskSpec.parse(next(t for t in bank['tasks'] if t['task_id'] == TASK), analysis_only=True)
    assert TaskSpec.parse(json.loads(json.dumps(task.declaration())), analysis_only=True) == task


def test_oracle_without_a_contract_is_rejected_at_parse(tmp_path):
    path = bank_with(tmp_path, declare=('oracle',))
    with pytest.raises(ValueError, match='requires a declared contract'):
        validate(path, verify_contents=False)


@pytest.mark.parametrize('bad', ['', 'ABC', 'A' * 64, 'a' * 63, 7, None], ids=[
    'empty', 'short', 'uppercase', 'one-short', 'integer', 'null'])
def test_malformed_identity_is_rejected_at_parse(tmp_path, bad):
    path = bank_with(tmp_path, edit=lambda _path, task: task.update(interaction_contract_sha256=bad))
    with pytest.raises(ValueError, match='lowercase SHA-256'):
        validate(path, verify_contents=False)


def test_contract_edited_after_it_was_declared_is_rejected(tmp_path):
    path = bank_with(tmp_path)
    edited = dict(CONTRACT, endpoint='interaction_boundary')
    (path.parent / 'graders' / TASK / 'interaction-contract.json').write_text(json.dumps(edited))
    redigest(path)
    with pytest.raises(ValueError, match='contract changed'):
        validate(path)


def test_contract_naming_another_task_is_rejected(tmp_path):
    path = bank_with(tmp_path, contract=dict(CONTRACT, task_id='regression'))
    with pytest.raises(ValueError, match='names another task'):
        validate(path)


def test_oracle_of_another_root_is_rejected(tmp_path):
    path = bank_with(tmp_path, oracle=dict(ORACLE, root_id='other-root'))
    with pytest.raises(ValueError, match='task or root differs'):
        validate(path)


def test_oracle_of_another_task_is_rejected(tmp_path):
    path = bank_with(tmp_path, oracle=dict(ORACLE, task_id='regression'))
    with pytest.raises(ValueError, match='task or root differs'):
        validate(path)


def test_oracle_bytes_edited_after_they_were_declared_are_rejected(tmp_path):
    path = bank_with(tmp_path)
    (path.parent / 'graders' / TASK / 'interaction-oracle.json').write_text(json.dumps(dict(ORACLE, reply='NEW')))
    redigest(path)
    with pytest.raises(ValueError, match='oracle bytes changed'):
        validate(path)


@pytest.mark.parametrize('name', ['interaction-contract.json', 'interaction-oracle.json'])
def test_declared_but_absent_file_is_rejected(tmp_path, name):
    path = bank_with(tmp_path)
    (path.parent / 'graders' / TASK / name).unlink()
    redigest(path)
    with pytest.raises(FileNotFoundError):
        validate(path)


@pytest.mark.parametrize('where', ['app/hint.txt', 'notes.md'], ids=['nested', 'top-level'])
def test_hidden_reply_inside_the_agent_visible_fixture_is_rejected(tmp_path, where):
    path = bank_with(tmp_path)
    target = path.parent / 'tasks' / TASK / where
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('the answer is ' + REPLY)
    redigest(path, 'fixture_digest', 'tasks')
    with pytest.raises(ValueError, match='hidden reply appears in the fixture'):
        validate(path)


def test_unread_declarations_are_not_checked_without_content_verification(tmp_path):
    path = bank_with(tmp_path, contract=dict(CONTRACT, task_id='regression'))
    assert validate(path, verify_contents=False)['interaction'] == dict(contracts=1, oracles=1)


@pytest.mark.parametrize('content,message', [
    (b'x' * 70000, 'bounded single-link regular file'),
    (b'not json', 'invalid interaction contract'),
    (b'{"task_id": "a", "task_id": "b"}', 'invalid interaction contract'),
    (b'[]', 'contract changed'),
], ids=['oversized', 'not-json', 'duplicate-keys', 'not-an-object'])
def test_unreadable_contract_file_is_rejected_not_trusted(tmp_path, content, message):
    path = bank_with(tmp_path)
    (path.parent / 'graders' / TASK / 'interaction-contract.json').write_bytes(content)
    redigest(path)
    with pytest.raises(ValueError, match=message):
        validate(path)
