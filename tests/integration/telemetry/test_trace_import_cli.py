"""`ahl trace-import` boundaries (#38): private output, no network, no referenced file opened, and explicit publication failures."""
import builtins
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import urllib.request

import pytest

from agent_harness_lab import cli
from agent_harness_lab.interop import atif, cli as trace_cli
from agent_harness_lab.interop.mapping import Rejected
from tests.support.atif_fixture import CANARY, agent_step, call, full, raw, trajectory

ROOT = Path(__file__).resolve().parents[3]


def source(tmp_path, document=None, name='trajectory.json'):
    path = tmp_path / name
    path.write_bytes(document if isinstance(document, bytes) else raw(trajectory() if document is None else document))
    return path


def run(*args):
    return cli.main(['trace-import', '--format', 'atif', *map(str, args)])


def arguments(path, output):
    return ['--input', path, '--output', output]


def refusal(capsys, *args):
    with pytest.raises(SystemExit) as stopped:
        run(*args)
    assert stopped.value.code == 2
    return capsys.readouterr().err


def test_import_writes_a_private_directory_holding_only_events_and_the_report(tmp_path, capsys):
    path, output = source(tmp_path, full()), tmp_path / 'out'
    before = path.read_bytes()
    assert run(*arguments(path, output)) == 0
    printed = json.loads(capsys.readouterr().out)
    assert sorted(os.listdir(output)) == ['conversion-report.json', 'events.v2.jsonl']
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert {stat.S_IMODE((output / name).stat().st_mode) for name in os.listdir(output)} == {0o600}
    assert json.loads((output / 'conversion-report.json').read_text()) == printed
    assert len((output / 'events.v2.jsonl').read_text().splitlines()) == printed['counts']['events']
    assert sorted(os.listdir(tmp_path)) == ['out', 'trajectory.json'] and path.read_bytes() == before


def test_importing_the_same_bytes_twice_produces_identical_files(tmp_path):
    path = source(tmp_path, full())
    for name in ('one', 'two'):
        assert run(*arguments(path, tmp_path / name)) == 0
    for name in ('events.v2.jsonl', 'conversion-report.json'):
        assert (tmp_path / 'one' / name).read_bytes() == (tmp_path / 'two' / name).read_bytes()


def test_the_module_entry_point_converts_and_refuses_with_exit_codes(tmp_path):
    env = {**os.environ, 'PYTHONPATH': str(ROOT)}
    base = [sys.executable, '-m', 'agent_harness_lab', 'trace-import', '--format', 'atif']
    done = subprocess.run([*base, *map(str, arguments(source(tmp_path), tmp_path / 'out'))], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and json.loads(done.stdout)['source_version'] == 'ATIF-v1.8' and (tmp_path / 'out' / 'events.v2.jsonl').is_file()
    refused = subprocess.run([*base, *map(str, arguments(source(tmp_path, b'[]', 'bad.json'), tmp_path / 'never'))], cwd=tmp_path, env=env,
                             capture_output=True, text=True, timeout=60)
    assert refused.returncode == 2 and 'unsupported_schema_version' in refused.stderr and not (tmp_path / 'never').exists()


def test_conversion_opens_no_socket_and_requests_no_url(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('network access during conversion')
    for owner, name in ((socket, 'socket'), (socket, 'create_connection'), (socket, 'getaddrinfo'), (urllib.request, 'urlopen')):
        monkeypatch.setattr(owner, name, denied)
    image = dict(type='image', source=dict(media_type='image/png', path='https://example.invalid/leak.png'))
    document = trajectory([dict(step_id=1, source='user', message=[image])], continued_trajectory_ref='https://example.invalid/next.json')
    assert run(*arguments(source(tmp_path, document), tmp_path / 'out')) == 0


def test_conversion_never_opens_a_file_the_trajectory_names(tmp_path, monkeypatch):
    names = ('secret.png', 'sub.json', 'next.json', 'audio.wav')
    for name in names:
        (tmp_path / name).write_bytes(b'sentinel')
    part = lambda kind, name: dict(type=kind, source=dict(media_type=kind + '/x', path=name))
    steps = [dict(step_id=1, source='user', message=[part('image', 'secret.png'), part('audio', str(tmp_path / 'audio.wav'))]),
             agent_step(2, [call('c1')], [dict(source_call_id='c1', subagent_trajectory_ref=[dict(trajectory_path=str(tmp_path / 'sub.json'))])])]
    path = source(tmp_path, trajectory(steps, continued_trajectory_ref='next.json'))
    opened = []
    real_os_open, real_open = os.open, builtins.open

    def spy_os(name, *args, **kwargs):
        opened.append(os.fspath(name))
        return real_os_open(name, *args, **kwargs)

    def spy_open(name, *args, **kwargs):
        opened.append(os.fspath(name) if not isinstance(name, int) else str(name))
        return real_open(name, *args, **kwargs)
    monkeypatch.setattr(os, 'open', spy_os)
    monkeypatch.setattr(builtins, 'open', spy_open)
    assert run(*arguments(path, tmp_path / 'out')) == 0
    assert opened and not [name for name in opened if os.path.basename(name) in names]


@pytest.mark.parametrize('body, reason', [(b'[]', 'unsupported_schema_version'), (b'{"schema_version": "ATIF-v1.7"}', 'unsupported_schema_version'),
    (b'{"schema_version": 1, "schema_version": 2}', 'duplicate_json_key'), (b'not json', 'invalid_json'), (b'\xff', 'invalid_json'),
    (raw(trajectory([dict(step_id=1, source='user', message='x', extra={CANARY: 1}, timestamp='soon')])), 'invalid_timestamp')],
    ids=['array', 'older-version', 'duplicate-key', 'not-json', 'not-utf8', 'bad-timestamp'])
def test_a_rejected_input_creates_nothing_and_echoes_no_input_value(tmp_path, capsys, body, reason):
    error = refusal(capsys, *arguments(source(tmp_path, body), tmp_path / 'out'))
    assert 'trace-import rejected: ' + reason in error and CANARY not in error
    assert sorted(os.listdir(tmp_path)) == ['trajectory.json']


def test_an_existing_output_is_refused_and_left_untouched(tmp_path, capsys):
    output = tmp_path / 'out'
    output.mkdir()
    (output / 'keep.txt').write_text('mine')
    assert 'output_exists' in refusal(capsys, *arguments(source(tmp_path), output))
    assert sorted(os.listdir(output)) == ['keep.txt'] and (output / 'keep.txt').read_text() == 'mine'


def test_an_output_path_with_a_parent_component_is_refused(tmp_path, capsys):
    (tmp_path / 'sub').mkdir()
    assert 'unsafe_output' in refusal(capsys, *arguments(source(tmp_path), tmp_path / 'sub' / '..' / 'out'))
    assert not (tmp_path / 'out').exists()


def test_an_output_beneath_a_symbolic_link_is_refused(tmp_path, capsys):
    (tmp_path / 'real').mkdir()
    (tmp_path / 'link').symlink_to(tmp_path / 'real')
    assert 'unsafe_output' in refusal(capsys, *arguments(source(tmp_path), tmp_path / 'link' / 'out'))
    assert os.listdir(tmp_path / 'real') == []


def linked(tmp_path):
    path = source(tmp_path)
    (tmp_path / 'link.json').symlink_to(path)
    return tmp_path / 'link.json'


def hardlinked(tmp_path):
    path = source(tmp_path)
    os.link(path, tmp_path / 'hard.json')
    return tmp_path / 'hard.json'


@pytest.mark.parametrize('make', [lambda tmp: tmp / 'absent.json', lambda tmp: tmp, linked, hardlinked], ids=['missing', 'directory', 'symlink', 'hardlink'])
def test_an_input_that_is_not_one_plain_regular_file_is_refused(tmp_path, capsys, make):
    assert 'input_unavailable' in refusal(capsys, *arguments(make(tmp_path), tmp_path / 'out'))
    assert not (tmp_path / 'out').exists()


def test_an_input_over_the_byte_bound_is_refused_before_it_is_read(tmp_path):
    with pytest.raises(Rejected) as stopped:
        trace_cli.import_trace('atif', source(tmp_path), tmp_path / 'out', atif.Limits(max_bytes=10))
    assert stopped.value.reason == 'input_too_large' and not (tmp_path / 'out').exists()


def test_an_unsupported_format_is_refused_by_the_parser(tmp_path, capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(['trace-import', '--format', 'otel-json', *map(str, arguments(source(tmp_path), tmp_path / 'out'))])
    assert stopped.value.code == 2 and 'invalid choice' in capsys.readouterr().err
    with pytest.raises(Rejected) as direct:
        trace_cli.import_trace('otel-json', source(tmp_path), tmp_path / 'out')
    assert direct.value.reason == 'unsupported_format'


def test_a_failed_write_leaves_no_output_and_a_private_stage(tmp_path, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(trace_cli, '_write_at', fail)
    assert 'output_unavailable' in refusal(capsys, *arguments(source(tmp_path), tmp_path / 'out'))
    stages = list(tmp_path.glob('.trace-import-*'))
    assert not (tmp_path/'out').exists() and len(stages) == 1 and stat.S_IMODE(stages[0].stat().st_mode) == 0o700


def test_concurrent_empty_output_is_not_replaced(tmp_path, monkeypatch):
    output = tmp_path/'out'
    original = trace_cli._write_at
    inode = []
    def write_and_race(directory, name, content):
        original(directory, name, content)
        if not inode:
            output.mkdir()
            inode.append(output.stat().st_ino)
    monkeypatch.setattr(trace_cli, '_write_at', write_and_race)
    with pytest.raises(Rejected) as caught:
        trace_cli.publish(output, {'events.v2.jsonl': b'example'})
    assert caught.value.reason == 'output_exists'
    assert output.stat().st_ino == inode[0] and list(output.iterdir()) == []


def test_missing_atomic_rename_fails_closed_without_output(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_cli.ctypes, 'CDLL', lambda *args, **kwargs: object())
    with pytest.raises(Rejected) as caught:
        trace_cli.publish(tmp_path/'out', {'events.v2.jsonl': b'example'})
    assert caught.value.reason == 'output_unavailable' and list(tmp_path.iterdir()) == []


def test_missing_output_parent_is_not_created(tmp_path):
    output = tmp_path/'missing'/'out'
    with pytest.raises(Rejected) as caught:
        trace_cli.publish(output, {'events.v2.jsonl': b'example'})
    assert caught.value.reason == 'output_unavailable' and not output.parent.exists()


def test_swapped_output_parent_does_not_redirect_publication(tmp_path, monkeypatch):
    parent, moved = tmp_path/'parent', tmp_path/'moved'
    parent.mkdir()
    output = parent/'out'
    original = trace_cli._write_at
    swapped = []
    def write_and_swap(directory, name, content):
        original(directory, name, content)
        if not swapped:
            parent.rename(moved)
            parent.mkdir()
            (parent/'intruder').write_text('untouched')
            swapped.append(True)
    monkeypatch.setattr(trace_cli, '_write_at', write_and_swap)
    with pytest.raises(Rejected):
        trace_cli.publish(output, {'events.v2.jsonl': b'one', 'conversion-report.json': b'two'})
    assert not output.exists()


def test_stage_name_collision_does_not_remove_existing_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_cli.secrets, 'token_hex', lambda _: 'fixed')
    existing = tmp_path/'.trace-import-fixed'
    existing.mkdir()
    inode = existing.stat().st_ino
    with pytest.raises(Rejected):
        trace_cli.publish(tmp_path/'out', {'events.v2.jsonl': b'example'})
    assert existing.is_dir() and existing.stat().st_ino == inode


def test_parent_sync_failure_reports_published_output(tmp_path, monkeypatch):
    original, identity = trace_cli.os.fsync, tmp_path.stat().st_ino
    def fail_parent(fd):
        if os.fstat(fd).st_ino == identity:
            raise OSError('sync failed')
        original(fd)
    monkeypatch.setattr(trace_cli.os, 'fsync', fail_parent)
    output = tmp_path/'out'
    with pytest.raises(Rejected) as caught:
        trace_cli.publish(output, {'events.v2.jsonl': b'example'})
    assert caught.value.reason == 'output_durability_unknown'
    assert (output/'events.v2.jsonl').read_bytes() == b'example'


def test_foreign_file_in_stage_is_not_deleted_after_name_collision(tmp_path, monkeypatch):
    original = trace_cli._write_at
    def collide(directory, name, content):
        fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(b'foreign')
        original(directory, name, content)
    monkeypatch.setattr(trace_cli, '_write_at', collide)
    with pytest.raises(Rejected):
        trace_cli.publish(tmp_path/'out', {'events.v2.jsonl': b'example'})
    stages = list(tmp_path.glob('.trace-import-*'))
    assert len(stages) == 1 and (stages[0]/'events.v2.jsonl').read_bytes() == b'foreign'


def test_cleanup_does_not_delete_a_replaced_staged_file(tmp_path, monkeypatch):
    original = trace_cli._write_at
    def replace_and_fail(directory, name, content):
        if name == 'conversion-report.json':
            os.unlink('events.v2.jsonl', dir_fd=directory)
            fd = os.open('events.v2.jsonl', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=directory)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(b'foreign')
            raise OSError('write failed')
        original(directory, name, content)
    monkeypatch.setattr(trace_cli, '_write_at', replace_and_fail)
    with pytest.raises(Rejected):
        trace_cli.publish(tmp_path/'out', {'events.v2.jsonl': b'one', 'conversion-report.json': b'two'})
    stages = list(tmp_path.glob('.trace-import-*'))
    assert len(stages) == 1 and (stages[0]/'events.v2.jsonl').read_bytes() == b'foreign'


def test_failed_stage_open_does_not_remove_replacement_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_cli.secrets, 'token_hex', lambda _: 'fixed')
    original = trace_cli.os.open
    def swap(name, flags, *args, **kwargs):
        if name == '.trace-import-fixed':
            stage = tmp_path/name
            stage.rename(tmp_path/'original-stage')
            stage.mkdir()
            raise OSError('stage replaced')
        return original(name, flags, *args, **kwargs)
    monkeypatch.setattr(trace_cli.os, 'open', swap)
    with pytest.raises(Rejected):
        trace_cli.publish(tmp_path/'out', {'events.v2.jsonl': b'example'})
    assert (tmp_path/'.trace-import-fixed').is_dir()


def test_replaced_staging_directory_is_not_published(tmp_path, monkeypatch):
    original = trace_cli._write_at
    def replace(directory, name, content):
        original(directory, name, content)
        stage = next(tmp_path.glob('.trace-import-*'))
        stage.rename(tmp_path/'original-stage')
        stage.mkdir()
        (stage/'foreign').write_text('untouched')
    monkeypatch.setattr(trace_cli, '_write_at', replace)
    with pytest.raises(Rejected):
        trace_cli.publish(tmp_path/'out', {'events.v2.jsonl': b'example'})
    assert not (tmp_path/'out').exists()
    assert next(tmp_path.glob('.trace-import-*')).joinpath('foreign').read_text() == 'untouched'
