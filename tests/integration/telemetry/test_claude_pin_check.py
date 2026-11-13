"""ahl sources claude pin-check: a names-only structural comparison; no capture value may reach its output."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.telemetry import claude_contract

FIXTURES = Path(__file__).resolve().parents[2]/'fixtures'/'claude_contract'
# Values planted in every position a value can occupy; none may appear in the output or in a skeleton.
SECRETS = ('SYNTH_SECRET_TEXT', '/home/synth-secret/path', 'synth-secret-id', 'SynthSecretToolName', 'synth_secret_value')


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(['sources', 'claude', 'pin-check', *argv])
    return code, out.getvalue(), err.getvalue()


def planted(version='2.1.318'):
    rows = [json.loads(line) for line in (FIXTURES/f'{version}.jsonl').read_text().splitlines()]
    for row in rows:
        row['cwd' if 'cwd' in row else 'sessionId'] = SECRETS[1]
        if 'uuid' in row: row['uuid'] = SECRETS[2]
        message = row.get('message')
        if isinstance(message, dict):
            message['content'] = SECRETS[0] if isinstance(message['content'], str) else [
                dict(block, **{key: SECRETS[0] for key in ('text', 'thinking', 'content') if key in block}, **({'name': SECRETS[3]} if 'name' in block else {}),
                    **({'input': {SECRETS[4]: SECRETS[0]}} if 'input' in block else {})) for block in message['content']]
    return rows


def capture(tmp_path, rows, name='capture.jsonl'):
    path = tmp_path/name; path.write_text(''.join(json.dumps(row)+'\n' for row in rows)); return str(path)


# 2.1.317 and later captures also carry kinds the adapter does not read (identity, link, mode, name and lineage records); they are counted, never parsed.
@pytest.mark.parametrize('version, verdict', (('2.1.316', 'compatible'), ('2.1.317', 'compatible_with_unknown_types'), ('2.1.318', 'compatible'),
    ('2.1.322', 'compatible_with_unknown_types')))
def test_the_verified_fixtures_are_compatible_with_their_own_contract(version, verdict):
    code, out, _ = run('--events', str(FIXTURES/f'{version}.jsonl'))
    report = json.loads(out)
    assert code == 0 and report['verdict'] == verdict and report['versions_outside_verified'] == []
    assert report['recorded_versions'] == {version: sum('version' in json.loads(line) for line in (FIXTURES/f'{version}.jsonl').read_text().splitlines())}
    assert report['values_read_into_output'] is False and report['message_text_stored'] is False


def test_the_current_release_fixture_lists_its_unknown_types_and_the_image_block_as_unknown():
    report = json.loads(run('--events', str(FIXTURES/'2.1.322.jsonl'))[1])
    unknown = {label for label, value in report['entry_types'].items() if value['status'] == 'unknown'}
    assert unknown == {'agent-name', 'fork-context-ref', 'permission-mode', 'pr-link', 'session-owner'}
    assert all(report['entry_types'][label]['reason'] == 'unknown_entry_type' for label in unknown)
    assert report['content_blocks']['image'] == dict(count=1, status='unknown') and report['newest_verified'] == '2.1.322'
    assert report['missing_required_keys'] == {} and report['block_missing_required_keys'] == {}


@pytest.mark.parametrize('version', ('2.1.319', '2.1.320', '2.1.321', '2.1.323'))
def test_a_release_with_no_fixture_is_named_as_outside_the_verified_versions_even_when_the_structure_matches(tmp_path, version):
    rows = [dict(row, version=version) if 'version' in row else row for row in planted('2.1.322')]
    report = json.loads(run('--events', capture(tmp_path, rows))[1])
    assert report['verdict'] == 'compatible_with_unknown_types' and report['versions_outside_verified'] == [version]
    assert 'VERIFIED_RANGES' in report['next_step']


def test_an_unknown_entry_type_reports_compatible_with_unknown_types_by_name_only(tmp_path):
    rows = planted()+[dict(type='future-kind', sessionId=SECRETS[2], payload=SECRETS[0]), dict(type='future-kind')]
    code, out, _ = run('--events', capture(tmp_path, rows))
    report = json.loads(out)
    assert code == 0 and report['verdict'] == 'compatible_with_unknown_types'
    assert report['entry_types']['future-kind'] == dict(count=2, status='unknown', reason='unknown_entry_type')
    assert report['entry_types']['mode' if 'mode' in report['entry_types'] else 'api-request']['status'] == 'declared_non_event'
    for secret in SECRETS: assert secret not in out  # grep test: no value of the capture reaches the output


def test_unknown_content_block_types_and_extra_keys_are_named_and_tolerated(tmp_path):
    rows = planted()
    rows[1]['message']['content'] = [dict(type='future-block', payload=SECRETS[0])]
    rows[1]['futureKey'] = SECRETS[0]
    code, out, _ = run('--events', capture(tmp_path, rows))
    report = json.loads(out)
    assert code == 0 and report['verdict'] == 'compatible_with_unknown_types'
    assert report['content_blocks']['future-block']['status'] == 'unknown' and 'futureKey' in report['unread_extra_keys']['user']
    assert SECRETS[0] not in out


def test_missing_required_fields_are_incompatible_and_exit_3(tmp_path):
    rows = planted()
    del rows[1]['uuid']
    removed = False
    for row in rows:
        for block in row['message']['content'] if row['type'] == 'assistant' and not removed else ():
            if block['type'] == 'tool_use': del block['name']; removed = True; break
    code, out, _ = run('--events', capture(tmp_path, rows))
    report = json.loads(out)
    assert code == 3 and report['verdict'] == 'incompatible'
    assert report['missing_required_keys']['user'] == {'uuid': 1} and report['block_missing_required_keys']['tool_use'] == {'name': 1}
    for secret in SECRETS: assert secret not in out


def test_malformed_unrecognizable_and_only_unknown_captures_are_never_compatible(tmp_path):
    for body in (b'not json\n', b'[1, 2]\n', b'{"type": "future-kind"}\n', b'', '{"type": "user", "x": "\u0000"'.encode()):
        path = tmp_path/'bad.jsonl'; path.write_bytes(body)
        code, out, _ = run('--events', str(path))
        assert json.loads(out)['verdict'] == 'incompatible' and code == 3


def test_a_release_outside_the_verified_versions_is_named_with_the_next_step(tmp_path):
    rows = [dict(row, version='2.1.399') if 'version' in row else row for row in planted()]
    report = json.loads(run('--events', capture(tmp_path, rows))[1])
    assert report['verdict'] == 'compatible' and report['versions_outside_verified'] == ['2.1.399']
    assert 'claude_contract' in report['next_step'] and 'VERIFIED_RANGES' in report['next_step']
    rows = [dict(row, version='SYNTH_SECRET_TEXT 1') if 'version' in row else row for row in planted()]
    assert 'SYNTH_SECRET_TEXT' not in run('--events', capture(tmp_path, rows, 'odd.jsonl'))[1]


def test_the_skeleton_keeps_names_and_invented_values_only_and_is_never_overwritten(tmp_path):
    rows = planted()+[dict(type='future-kind', payload=SECRETS[0], **{'SYNTH SECRET key': 1})]
    target = tmp_path/'skeleton.jsonl'
    code, out, _ = run('--events', capture(tmp_path, rows), '--emit-skeleton', str(target))
    text = target.read_text()
    assert code == 0 and target.stat().st_mode & 0o777 == 0o600
    for secret in (*SECRETS, 'SYNTH SECRET'): assert secret not in text
    skeleton = [json.loads(line) for line in text.splitlines()]
    assert {row['type'] for row in skeleton} >= {'user', 'assistant', 'attachment', 'future-kind'}
    assert all(row.get('cwd', '/synthetic/project') == '/synthetic/project' for row in skeleton)
    # The skeleton is itself structurally compatible with the contract apart from the unknown kind.
    assert json.loads(run('--events', str(target))[1])['verdict'] == 'compatible_with_unknown_types'
    code, _, err = run('--events', capture(tmp_path, rows), '--emit-skeleton', str(target))
    assert code == 2 and 'FileExistsError' in err and target.read_text() == text


def test_invalid_input_exits_2_and_a_symlink_or_directory_is_refused(tmp_path):
    real = Path(capture(tmp_path, planted())); link = tmp_path/'link.jsonl'; link.symlink_to(real)
    for target in (link, tmp_path, tmp_path/'missing.jsonl'):
        code, out, err = run('--events', str(target))
        assert code == 2 and out == ''
    assert main(['sources', 'claude']) == 2


def test_names_that_are_not_identifiers_are_never_printed(tmp_path):
    rows = planted(); rows[1]['SYNTH SECRET key'] = 1; rows[1]['type'] = 'user'
    rows.append({'type': 'SYNTH SECRET type'})
    out = run('--events', capture(tmp_path, rows))[1]
    assert 'SYNTH SECRET' not in out and 'unrecognized' in out
    assert claude_contract.name('ok-name_1') == 'ok-name_1' and claude_contract.name('a b') == 'unrecognized' and claude_contract.name(7) == 'unrecognized'
