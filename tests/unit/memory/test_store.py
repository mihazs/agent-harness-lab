"""Opt-in private memory store: durability, corruption, isolation, versioning, bounds and cleanup (synthetic data)."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from agent_harness_lab.cli import main as ahl
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.memory import schema
from agent_harness_lab.memory.store import (IndexUnavailable, MemoryStore, MemoryStoreError, ReadOnlyStore, StoreBusy,
                                            fts5_available)

ROOT = Path(__file__).resolve().parents[3]
EVAL = {'table': 'evaluations', 'row': {'evaluation_id': 'e1', 'result': None, 'outcome': 'pass'}}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def store(tmp_path):
    return MemoryStore.init(tmp_path / 'store')


def object_file(store, raw):
    digest = sha(raw)
    return store.root / 'objects/sha256' / digest[:2] / digest


def evaluation_records(data, outcome='pass', eid='e1'):
    return [{'table': 'evaluations', 'row': {'evaluation_id': eid, 'result': sha(data), 'outcome': outcome}}]


# ---- layout and isolation -------------------------------------------------------------------------------------
def test_init_creates_owner_only_layout_without_secrets(store):
    assert {p.name for p in store.root.iterdir()} == {'objects', 'locks', 'store.json', 'index.sqlite'}
    assert store.root.stat().st_mode & 0o777 == 0o700
    meta = json.loads((store.root / 'store.json').read_text())
    assert set(meta) == {'schema', 'format_version', 'store_id'} and meta['store_id'] == store.store_id
    assert MemoryStore.open(store.root).store_id == store.store_id


def test_init_refuses_existing_store_and_foreign_files(store, tmp_path):
    with pytest.raises(MemoryStoreError, match='new or empty'):
        MemoryStore.init(store.root)
    (tmp_path / 'other').mkdir()
    (tmp_path / 'other/notes.txt').write_text('x')
    with pytest.raises(MemoryStoreError, match='new or empty'):
        MemoryStore.init(tmp_path / 'other')


def test_unfinished_init_can_be_repeated(tmp_path):
    (tmp_path / 's/objects/sha256').mkdir(parents=True, mode=0o700)
    (tmp_path / 's').chmod(0o700)
    assert MemoryStore.init(tmp_path / 's').status()['index'] == 'ok'


def test_relative_dotdot_and_symlinked_paths_are_refused(tmp_path):
    real = tmp_path / 'real'
    real.mkdir()
    (tmp_path / 'link').symlink_to(real)
    for bad in ('relative/store', str(tmp_path / 'a/../b'), str(tmp_path / 'link/store'), str(tmp_path / 'link')):
        with pytest.raises(MemoryStoreError, match='absolute|symlink'):
            MemoryStore.init(bad)


def test_store_inside_a_checkout_is_refused(tmp_path):
    (tmp_path / 'checkout/.git').mkdir(parents=True)
    with pytest.raises(MemoryStoreError, match='checkout'):
        MemoryStore.init(tmp_path / 'checkout/sub/store')


def test_store_inside_an_existing_controller_store_is_refused(tmp_path):
    with HistoryStore(tmp_path / 'history').locked(create=True):
        pass
    for marker in (tmp_path / 'history', tmp_path / 'history/nested'):
        with pytest.raises(MemoryStoreError, match='protected controller store'):
            MemoryStore.init(marker / 'memory', excluded=[])
    assert MemoryStore.init(tmp_path / 'sibling').status()['index'] == 'ok'


@pytest.mark.parametrize('protected', ['inside', 'contains', 'same'])
def test_store_overlapping_protected_directories_is_refused(tmp_path, protected):
    target = tmp_path / 'protected'
    (target / 'nested').mkdir(parents=True)
    other = {'inside': target, 'contains': target / 'nested', 'same': tmp_path / 'store'}[protected]
    root = {'inside': target / 'store', 'contains': target, 'same': tmp_path / 'store'}[protected]
    with pytest.raises(MemoryStoreError, match='overlaps'):
        MemoryStore.init(root, excluded=[other])


def test_excluded_symlink_is_resolved_before_comparison(tmp_path):
    (tmp_path / 'project').mkdir()
    (tmp_path / 'alias').symlink_to(tmp_path / 'project')
    with pytest.raises(MemoryStoreError, match='overlaps'):
        MemoryStore.init(tmp_path / 'project/store', excluded=[tmp_path / 'alias'])


def test_group_accessible_store_is_refused(store):
    store.root.chmod(0o750)
    with pytest.raises(MemoryStoreError, match='owner-only'):
        MemoryStore.open(store.root)


def test_open_rejects_unrelated_directory(tmp_path):
    (tmp_path / 'd').mkdir(mode=0o700)
    (tmp_path / 'd/store.json').write_text('{"schema": "other"}')
    with pytest.raises(MemoryStoreError, match='not an AHL memory store'):
        MemoryStore.open(tmp_path / 'd')


# ---- publication and integrity ---------------------------------------------------------------------------------
def test_publish_round_trip_is_idempotent_and_hash_addressed(store):
    digest = store.publish('p1', 'report', b'synthetic body')
    assert digest == sha(b'synthetic body') and store.read(digest) == b'synthetic body'
    assert store.publish('p1', 'report', b'synthetic body') == digest
    assert store.artifacts('p1') == {'artifacts': [{'digest': digest, 'kind': 'report', 'size': 14}], 'truncated': False}
    assert object_file(store, b'synthetic body').stat().st_mode & 0o777 == 0o400
    assert store.verify()['ok'] and store.status()['artifacts'] == 1


def test_same_bytes_in_two_projects_share_one_object(store):
    store.publish('p1', 'report', b'shared')
    store.publish('p2', 'report', b'shared')
    assert store.status()['artifacts'] == 2 and store.status()['object_files'] == 3 and store.verify()['ok']


def test_artifact_cannot_impersonate_a_manifest(store):
    with pytest.raises(MemoryStoreError, match='impersonate'):
        store.publish('p1', 'report', b'ahl-memory-manifest-v1\n{}')


def test_dependencies_must_exist_and_rejected_publication_leaves_nothing_durable(store):
    before = sorted(store.root.rglob('*'))
    with pytest.raises(MemoryStoreError, match='unknown'):
        store.publish('p1', 'report', b'child', dependencies=[sha(b'missing')])
    assert sorted(store.root.rglob('*')) == before
    parent = store.publish('p1', 'capture', b'parent')
    child = store.publish('p1', 'analysis', b'child', dependencies=[parent])
    with store._db() as db:
        assert db.execute('SELECT dependency FROM dependencies WHERE dependent=?', (child,)).fetchall() == [(parent,)]


@pytest.mark.parametrize('kwargs', [
    dict(kind='bogus'), dict(project_id='bad project'), dict(project_id='x' * 129),
    dict(records=[{'table': 'artifacts', 'row': {'digest': 'x'}}]),
    dict(records=[{'table': 'evaluations', 'row': {'evaluation_id': 'e', 'result': None, 'outcome': 'maybe'}}]),
    dict(records=[{'table': 'evaluations', 'row': {'evaluation_id': 'e', 'result': None, 'outcome': 'pass', 'detail_json': [1]}}]),
    dict(records=[{'table': 'metrics', 'row': {'evaluation_id': 'e', 'name': 'm', 'status': 'unknown', 'value': 0.0}}]),
    dict(records=[{'table': 'evaluations', 'row': {'evaluation_id': 'e', 'result': None, 'outcome': 'pass', 'external_state': 'verified'}}]),
    dict(records=[{'table': 'evaluations', 'row': {'evaluation_id': 'e', 'result': None, 'outcome': float('nan')}}]),
])
def test_invalid_records_are_refused_before_anything_is_written(store, kwargs):
    arguments = dict(project_id='p1', kind='report', data=b'body')
    arguments.update(kwargs)
    with pytest.raises(MemoryStoreError):
        store.publish(arguments['project_id'], arguments['kind'], arguments['data'], records=arguments.get('records', ()))
    assert not list((store.root / 'objects/sha256').iterdir())


def test_records_are_indexed_and_unknown_metrics_are_not_zero(store):
    data = b'result'
    records = evaluation_records(data) + [
        {'table': 'metrics', 'row': {'evaluation_id': 'e1', 'name': 'latency', 'status': 'unknown', 'value': None}},
        {'table': 'metrics', 'row': {'evaluation_id': 'e1', 'name': 'score', 'status': 'measured', 'value': 0.0}}]
    store.publish('p1', 'evaluation', data, records=records)
    with store._db() as db:
        assert db.execute('SELECT name, value, status FROM metrics ORDER BY name').fetchall() == [
            ('latency', None, 'unknown'), ('score', 0.0, 'measured')]
        assert db.execute('SELECT external_state FROM evaluations').fetchone() == ('unverified',)


def test_comparison_requires_two_distinct_existing_evaluations(store):
    a, b = b'baseline', b'candidate'
    store.publish('p1', 'evaluation', a, records=evaluation_records(a, eid='base'))
    store.publish('p1', 'evaluation', b, records=evaluation_records(b, eid='cand'))
    ok = {'table': 'comparisons', 'row': {'comparison_id': 'c1', 'baseline': 'base', 'candidate': 'cand', 'status': 'unknown'}}
    store.publish('p1', 'comparison', b'cmp', records=[ok])
    bad = dict(ok, row=dict(ok['row'], comparison_id='c2', candidate='ghost'))
    with pytest.raises(MemoryStoreError, match='unknown'):
        store.publish('p1', 'comparison', b'cmp2', records=[bad])
    same = dict(ok, row=dict(ok['row'], comparison_id='c3', candidate='base'))
    with pytest.raises(MemoryStoreError):
        store.publish('p1', 'comparison', b'cmp3', records=[same])


def test_findings_link_evidence_and_levels_are_enforced(store):
    data = b'evidence'
    store.publish('p1', 'report', data, records=[
        {'table': 'findings', 'row': {'finding_id': 'f1', 'evidence': sha(data), 'level': 'observed'}},
        {'table': 'finding_links', 'row': {'finding_id': 'f1', 'target_type': 'artifact', 'target_id': sha(data)}}])
    with pytest.raises(MemoryStoreError):
        store.publish('p1', 'report', b'x', records=[{'table': 'findings', 'row': {'finding_id': 'f2', 'evidence': sha(b'x'), 'level': 'proven'}}])


def test_snapshot_and_stage_records(store):
    data = b'snap'
    store.publish('p1', 'manifest', data, records=[
        {'table': 'snapshots', 'row': {'snapshot_id': 's1', 'manifest': sha(data)}},
        {'table': 'stages', 'row': {'snapshot_id': 's1', 'stage': 'capture', 'algorithm_version': 'v1', 'output': None, 'status': 'unknown'}}])
    with pytest.raises(MemoryStoreError):  # a complete stage must name its output
        store.publish('p1', 'manifest', b'snap2', records=[
            {'table': 'snapshots', 'row': {'snapshot_id': 's2', 'manifest': sha(b'snap2')}},
            {'table': 'stages', 'row': {'snapshot_id': 's2', 'stage': 'capture', 'algorithm_version': 'v1', 'output': None, 'status': 'complete'}}])


def test_corrupted_object_is_detected_and_never_used(store):
    digest = store.publish('p1', 'report', b'good')
    path = object_file(store, b'good')
    path.chmod(0o600)
    path.write_bytes(b'evil')
    with pytest.raises(MemoryStoreError, match='integrity'):
        store.read(digest)
    report = store.verify()
    assert not report['ok'] and 'integrity' in ' '.join(report['issues'])
    assert 'blocked' in ' '.join(report['issues'])  # its manifest cannot be trusted either
    assert store.reindex()['manifests'] == 0 and store.artifacts('p1')['artifacts'] == []
    assert store.publish('p1', 'report', b'good') == digest  # re-publishing identical bytes repairs the object
    assert store.read(digest) == b'good' and store.verify()['ok']


def test_planted_special_files_links_and_hardlinks_are_refused(store, tmp_path):
    digest = store.publish('p1', 'report', b'body')
    path = object_file(store, b'body')
    outside = tmp_path / 'outside'
    outside.write_bytes(b'body')
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(MemoryStoreError, match='refused'):
        store.read(digest)
    path.unlink()
    os.link(outside, path)
    with pytest.raises(MemoryStoreError, match='single-link'):
        store.read(digest)
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(MemoryStoreError, match='refused'):
        store.read(digest)
    path.unlink()
    path.mkdir()
    with pytest.raises(MemoryStoreError, match='refused'):
        store.read(digest)
    issues = ' '.join(store.verify()['issues'])
    assert 'refused' in issues
    path.rmdir()
    (store.root / 'objects/sha256/zz').symlink_to(tmp_path)
    (store.root / 'stray').write_text('x')
    issues = ' '.join(store.verify()['issues'])
    assert 'zz' in issues and 'stray' in issues


def test_symlinked_or_hardlinked_index_is_refused(store, tmp_path):
    index = store.root / 'index.sqlite'
    other = tmp_path / 'copy.sqlite'
    os.link(index, other)
    with pytest.raises(IndexUnavailable, match='single-link'):
        store.artifacts('p1')
    other.unlink()
    moved = tmp_path / 'moved.sqlite'
    index.rename(moved)
    index.symlink_to(moved)
    with pytest.raises(IndexUnavailable):
        store.artifacts('p1')


def test_oversize_artifact_and_bounded_counts(tmp_path):
    small = MemoryStore.init(tmp_path / 's', max_object_bytes=8)
    with pytest.raises(MemoryStoreError, match='bound'):
        small.publish('p1', 'report', b'123456789')
    files = MemoryStore.init(tmp_path / 'f', max_files=4)
    files.publish('p1', 'report', b'a')
    files.publish('p1', 'report', b'b')
    with pytest.raises(MemoryStoreError, match='bound'):
        files.publish('p1', 'report', b'c')
    total = MemoryStore.init(tmp_path / 't', max_bytes=5)
    total.publish('p1', 'report', b'12345')
    with pytest.raises(MemoryStoreError, match='bound'):
        total.publish('p1', 'report', b'6')


def test_scan_stops_at_the_file_count_bound(store):
    for number in range(3):
        store.publish('p1', 'report', str(number).encode())
    store.max_files = 2
    with pytest.raises(MemoryStoreError, match='file count'):
        store.verify()


def test_list_results_are_bounded(store):
    for number in range(3):
        store.publish('p1', 'report', str(number).encode())
    page = store.artifacts('p1', limit=2)
    assert len(page['artifacts']) == 2 and page['truncated'] is True
    assert store.artifacts('p1', limit=10 ** 9)['truncated'] is False


def test_per_project_row_ceiling_is_enforced_by_the_schema(monkeypatch):
    monkeypatch.setitem(schema.ROW_LIMITS, 'artifacts', 2)
    connection = sqlite3.connect(':memory:', isolation_level=None)
    schema.migrate(connection, (schema._v1(),))
    connection.execute("INSERT INTO projects VALUES ('p1')")
    for number in range(2):
        connection.execute('INSERT INTO artifacts VALUES (?,?,?,?,?,?)', ('p1', f'{number:064x}', 'f' * 64, 'report', 1, 'published'))
    with pytest.raises(sqlite3.DatabaseError, match='ceiling'):
        connection.execute('INSERT INTO artifacts VALUES (?,?,?,?,?,?)', ('p1', '9' * 64, 'f' * 64, 'report', 1, 'published'))


# ---- crash and recovery -----------------------------------------------------------------------------------------
CRASH = textwrap.dedent('''
    import hashlib, os, sys
    from agent_harness_lab.memory.store import MemoryStore
    sha = lambda raw: hashlib.sha256(raw).hexdigest()
    store = MemoryStore.open(sys.argv[1])
    step = sys.argv[2]
    def hook(name):
        if name == step:
            os._exit(9)  # no cleanup, like SIGKILL
    store._hook = hook
    store.publish('p1', 'report', b'doomed', records=[{'table': 'evaluations', 'row': {'evaluation_id': 'e1', 'result': sha(b'doomed'), 'outcome': 'pass'}}])
''')


def killed_publish(store, step):
    environment = dict(os.environ, PYTHONPATH=str(ROOT))
    done = subprocess.run([sys.executable, '-c', CRASH, str(store.root), step], env=environment, capture_output=True)
    assert done.returncode == 9, done.stderr


@pytest.mark.parametrize('step', ['before_rename:' + sha(b'doomed'), 'after_object'])
def test_killed_write_before_the_manifest_leaves_no_record(store, step):
    killed_publish(store, 'before_rename:' + sha(b'doomed') if step.startswith('before') else step)
    assert store.artifacts('p1')['artifacts'] == [] and store.status()['artifacts'] == 0
    assert store.reindex()['manifests'] == 0 and store.artifacts('p1')['artifacts'] == []
    plan = store.gc()
    assert plan['orphan_count'] + plan['partial_count'] == 1  # the half-written blob or temp file is only garbage
    store.gc(apply=True)
    assert store.verify()['ok']


def test_killed_write_after_the_manifest_is_recovered_by_reindex_not_resurrected_partially(store):
    killed_publish(store, 'after_manifest')
    assert store.artifacts('p1')['artifacts'] == []
    report = store.verify()
    assert not report['ok'] and 'not indexed' in ' '.join(report['issues'])
    assert store.gc()['orphan_count'] == 0  # the durable manifest protects its artifact
    assert store.reindex()['rebuilt'] is True
    assert [a['digest'] for a in store.artifacts('p1')['artifacts']] == [sha(b'doomed')]
    assert store.read(sha(b'doomed')) == b'doomed' and store.verify()['ok']


def test_retry_with_different_records_after_a_killed_publish_does_not_break_reindex(store):
    killed_publish(store, 'after_manifest')
    store.publish('p1', 'report', b'doomed', records=evaluation_records(b'doomed', outcome='fail'))
    report = store.reindex()
    assert report['rebuilt'] is True and report['manifests'] == 1 and report['issue_count'] == 1
    assert 'already describes artifact' in report['issues'][0]
    assert [a['digest'] for a in store.artifacts('p1')['artifacts']] == [sha(b'doomed')]
    assert store.read(sha(b'doomed')) == b'doomed'


def test_killed_manifest_rename_leaves_a_temp_file_not_a_record(store):
    def hook(name):
        if name.startswith('before_rename:') and name != 'before_rename:' + sha(b'doomed'):
            raise KeyboardInterrupt
    store._hook = hook
    with pytest.raises(KeyboardInterrupt):
        store.publish('p1', 'report', b'doomed')
    assert store.artifacts('p1')['artifacts'] == []
    store._hook = None
    plan = store.gc()
    assert plan['orphan_count'] == 1 and plan['partial_count'] == 0  # blob without a manifest; the temp file was cleaned
    assert store.gc(apply=True)['applied'] and store.verify()['ok']


@pytest.mark.parametrize('damage', ['garbage', 'missing', 'wrong_store'])
def test_damaged_index_is_rebuilt_from_objects(store, damage, tmp_path):
    data = b'result'
    parent = store.publish('p1', 'capture', b'parent')
    store.publish('p1', 'evaluation', data, dependencies=[parent], records=evaluation_records(data))
    before = store.artifacts('p1')
    index = store.root / 'index.sqlite'
    for suffix in ('-wal', '-shm'):
        Path(str(index) + suffix).unlink(missing_ok=True)
    if damage == 'garbage':
        index.write_bytes(b'not a database' * 100)
    elif damage == 'missing':
        index.unlink()
    else:
        other = MemoryStore.init(tmp_path / 'other')
        index.unlink()
        (other.root / 'index.sqlite').rename(index)
    assert store.status()['artifacts'] is None  # unknown, never zero
    assert not store.verify()['ok']
    with pytest.raises(IndexUnavailable):
        store.artifacts('p1')
    assert store.reindex(dry_run=True)['rebuilt'] is False and not store.verify()['ok']
    assert store.reindex()['rebuilt'] is True
    assert store.artifacts('p1') == before and store.verify()['ok']
    with store._db() as db:
        assert db.execute('SELECT count(*) FROM dependencies').fetchone() == (1,)
        assert db.execute('SELECT outcome FROM evaluations').fetchone() == ('pass',)
    assert not (store.root / 'index.sqlite.rebuild').exists()


def test_reindex_blocks_records_whose_dependency_is_lost(store):
    parent = store.publish('p1', 'capture', b'parent')
    store.publish('p1', 'analysis', b'child', dependencies=[parent])
    path = object_file(store, b'parent')
    path.chmod(0o600)
    path.unlink()
    report = store.reindex()
    assert report['manifests'] == 0
    assert any('blocked' in text for text in report['issues'])
    assert sha(b'child') not in [a['digest'] for a in store.artifacts('p1')['artifacts']]


def test_failed_rebuild_keeps_the_old_index(store, monkeypatch):
    store.publish('p1', 'report', b'body')
    original = (store.root / 'index.sqlite').read_bytes()
    monkeypatch.setattr(MemoryStore, '_apply', staticmethod(lambda *a: (_ for _ in ()).throw(MemoryStoreError('boom'))))
    with pytest.raises(MemoryStoreError, match='boom'):
        store.reindex()
    monkeypatch.undo()
    assert (store.root / 'index.sqlite').read_bytes() == original
    assert store.artifacts('p1')['artifacts']


# ---- cleanup -----------------------------------------------------------------------------------------------------
def test_gc_removes_only_unreferenced_objects_deterministically(store):
    keep = store.publish('p1', 'report', b'keep')
    store._put(b'orphan-b')
    store._put(b'orphan-a')
    plan = store.gc()
    assert plan['orphans'] == sorted([sha(b'orphan-a'), sha(b'orphan-b')]) and plan['applied'] is False
    assert object_file(store, b'orphan-a').exists()  # a plan changes nothing
    assert store.gc(apply=True)['applied'] is True
    assert not object_file(store, b'orphan-a').exists() and not object_file(store, b'orphan-b').exists()
    assert store.read(keep) == b'keep' and store.verify()['ok'] and store.gc()['orphan_count'] == 0


def test_gc_keeps_referenced_dependencies_and_refuses_when_the_store_is_damaged(store):
    parent = store.publish('p1', 'capture', b'parent')
    store.publish('p1', 'analysis', b'child', dependencies=[parent])
    store._put(b'orphan')
    store.gc(apply=True)
    assert store.read(parent) == b'parent' and store.verify()['ok']
    (store.root / 'stray').write_text('x')
    store._put(b'orphan2')
    with pytest.raises(MemoryStoreError, match='refused'):
        store.gc(apply=True)
    assert object_file(store, b'orphan2').exists()


def test_gc_never_deletes_when_a_manifest_is_unreadable(store):
    store.publish('p1', 'report', b'body')
    manifest = next(p for p in (store.root / 'objects/sha256').rglob('*') if p.is_file() and p.read_bytes().startswith(b'ahl-memory-manifest'))
    manifest.chmod(0o600)
    manifest.write_bytes(b'ahl-memory-manifest-v1\n{broken')
    with pytest.raises(MemoryStoreError, match='refused'):
        store.gc(apply=True)
    assert object_file(store, b'body').exists()


# ---- versions ---------------------------------------------------------------------------------------------------
def test_each_migration_step_upgrades_the_previous_schema():
    assert schema.VERSION == len(schema.MIGRATIONS)
    connection = sqlite3.connect(':memory:', isolation_level=None)
    for number in range(1, schema.VERSION + 1):
        schema.migrate(connection, schema.MIGRATIONS[:number])
        assert connection.execute('PRAGMA user_version').fetchone()[0] == number
    assert {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {
        'projects', 'artifacts', 'snapshots', 'stages', 'dependencies', 'evaluations', 'metrics', 'comparisons',
        'findings', 'finding_links'}
    assert schema.migrate(connection) == schema.VERSION  # idempotent


def test_migration_is_forward_only(store):
    connection = sqlite3.connect(':memory:', isolation_level=None)
    connection.execute('PRAGMA user_version = 7')
    with pytest.raises(ValueError, match='newer'):
        schema.migrate(connection)


def test_future_index_schema_opens_read_only_with_a_clear_error(store):
    digest = store.publish('p1', 'report', b'body')
    connection = sqlite3.connect(store.root / 'index.sqlite', isolation_level=None)
    connection.execute('PRAGMA user_version = 99')
    connection.close()
    reopened = MemoryStore.open(store.root)
    assert 'newer than supported' in reopened.read_only_reason and reopened.status()['read_only']
    assert reopened.artifacts('p1')['artifacts'][0]['digest'] == digest  # reads keep working
    assert reopened.read(digest) == b'body'
    for action in (lambda: reopened.publish('p1', 'report', b'new'), reopened.reindex, lambda: reopened.gc(apply=True)):
        with pytest.raises(ReadOnlyStore, match='newer than supported'):
            action()
    assert sqlite3.connect(store.root / 'index.sqlite').execute('PRAGMA user_version').fetchone() == (99,)


def test_future_store_format_is_read_only(store):
    path = store.root / 'store.json'
    meta = json.loads(path.read_text())
    meta['format_version'] = 2
    path.write_text(json.dumps(meta))
    reopened = MemoryStore.open(store.root)
    assert 'store format 2' in reopened.read_only_reason
    with pytest.raises(ReadOnlyStore):
        reopened.publish('p1', 'report', b'x')
    assert reopened.status()['index'] == 'ok'


def test_index_without_a_schema_is_unavailable_until_reindex(store):
    connection = sqlite3.connect(store.root / 'index.sqlite', isolation_level=None)
    connection.execute('PRAGMA user_version = 0')
    connection.close()
    with pytest.raises(IndexUnavailable, match='no schema'):
        store.artifacts('p1')


# ---- concurrency ------------------------------------------------------------------------------------------------
def test_only_one_writer_at_a_time(store):
    store.busy_seconds = 0.2
    descriptor = os.open(store.root / 'locks/writer.lock', os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    try:
        with pytest.raises(StoreBusy):
            store.publish('p1', 'report', b'x')
        assert store.verify()['index'] == 'ok'  # readers are not blocked
    finally:
        os.close(descriptor)
    store.publish('p1', 'report', b'x')


def test_readers_see_one_consistent_snapshot(store):
    store.publish('p1', 'report', b'one')
    other = MemoryStore.open(store.root)
    with store._db() as reader:
        assert reader.execute('SELECT count(*) FROM artifacts').fetchone() == (1,)
        other.publish('p1', 'report', b'two')
        assert reader.execute('SELECT count(*) FROM artifacts').fetchone() == (1,)
    assert store.status()['artifacts'] == 2


def test_backup_is_a_consistent_private_copy(store, tmp_path):
    store.publish('p1', 'report', b'body')
    target = Path(store.backup(tmp_path / 'backup.sqlite'))
    assert target.stat().st_mode & 0o777 == 0o600
    assert sqlite3.connect(target).execute('SELECT count(*) FROM artifacts').fetchone() == (1,)
    with pytest.raises(MemoryStoreError, match='exist'):
        store.backup(target)


# ---- command line -------------------------------------------------------------------------------------------------
def run(capsys, *args):
    try:
        code = ahl(['memory', *args])
    except SystemExit as exit_:
        code = exit_.code
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_lifecycle_and_exit_codes(tmp_path, capsys):
    path = str(tmp_path / 'cli-store')
    assert run(capsys, 'status', '--store', path)[0] == 2  # not initialized
    code, out, _ = run(capsys, 'init', '--store', path)
    assert code == 0 and json.loads(out)['index'] == 'ok'
    assert run(capsys, 'init', '--store', path)[0] == 2
    store = MemoryStore.open(path)
    store.publish('p1', 'report', b'body')
    code, out, _ = run(capsys, 'verify', '--store', path)
    assert code == 0 and json.loads(out)['ok'] is True
    assert json.loads(run(capsys, 'status', '--store', path)[1])['artifacts'] == 1
    store._put(b'orphan')
    plan = json.loads(run(capsys, 'gc', '--store', path)[1])
    assert plan['orphan_count'] == 1 and plan['applied'] is False
    assert json.loads(run(capsys, 'gc', '--store', path, '--apply')[1])['applied'] is True
    assert json.loads(run(capsys, 'reindex', '--store', path, '--dry-run')[1])['rebuilt'] is False
    (Path(path) / 'index.sqlite').write_bytes(b'garbage' * 50)
    assert run(capsys, 'verify', '--store', path)[0] == 3
    assert json.loads(run(capsys, 'reindex', '--store', path)[1])['rebuilt'] is True
    assert run(capsys, 'verify', '--store', path)[0] == 0


def test_fts5_runtime_check_reports_true_false_or_unknown(store, monkeypatch):
    assert store.status()['fts5'] is fts5_available() and isinstance(fts5_available(), bool)

    def refuse(message):
        def connect(*_, **__):
            raise sqlite3.OperationalError(message)
        monkeypatch.setattr(sqlite3, 'connect', connect)
    refuse('no such module: fts5')
    assert fts5_available() is False
    refuse('disk I/O error')  # the probe failed: unknown, never "absent"
    assert fts5_available() is None


def test_cli_accepts_json_and_a_gc_dry_run_that_never_deletes(tmp_path, capsys):
    path = str(tmp_path / 'cli-store')
    assert run(capsys, 'init', '--store', path, '--json')[0] == 0
    MemoryStore.open(path)._put(b'orphan')
    plan = json.loads(run(capsys, 'gc', '--store', path, '--dry-run', '--json')[1])
    assert plan['orphan_count'] == 1 and plan['applied'] is False
    assert run(capsys, 'gc', '--store', path, '--dry-run', '--apply')[0] == 2
    assert run(capsys, 'status', '--store', path, '--dry-run')[0] == 2
    assert json.loads(run(capsys, 'status', '--store', path)[1])['fts5'] in (True, False, None)


def test_cli_requires_an_explicit_location_and_valid_flags(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv('AHL_MEMORY_STORE', raising=False)
    assert run(capsys, 'status')[0] == 2
    assert run(capsys, 'status', '--store', str(tmp_path), '--apply')[0] == 2
    assert run(capsys, 'init', '--store', str(tmp_path / 'a/b'), '--exclude', str(tmp_path))[0] == 2
    monkeypatch.setenv('AHL_MEMORY_STORE', str(tmp_path / 'env-store'))
    assert run(capsys, 'init')[0] == 0


def test_cli_reports_read_only_stores_as_blocked(tmp_path, capsys):
    store = MemoryStore.init(tmp_path / 's')
    connection = sqlite3.connect(store.root / 'index.sqlite', isolation_level=None)
    connection.execute('PRAGMA user_version = 99')
    connection.close()
    code, _, err = run(capsys, 'reindex', '--store', str(store.root))
    assert code == 3 and 'newer than supported' in err
    assert run(capsys, 'status', '--store', str(store.root))[0] == 0


def test_cli_store_under_a_frozen_project_is_refused(tmp_path, capsys):
    (tmp_path / '.ahl-read-only').write_text('')
    code, _, err = run(capsys, 'init', '--store', str(tmp_path / 'store'))
    assert code == 2 and 'read-only' in err


def test_retry_with_different_dependencies_after_a_killed_publish_does_not_break_reindex(store):
    store.publish('p1', 'report', b'base')
    killed_publish(store, 'after_manifest')
    store.publish('p1', 'report', b'doomed', dependencies=[sha(b'base')], records=evaluation_records(b'doomed'))
    report = store.reindex()
    assert report['rebuilt'] is True and report['manifests'] == 2 and report['issue_count'] == 1
    assert 'already describes artifact' in report['issues'][0]
    assert sorted(a['digest'] for a in store.artifacts('p1')['artifacts']) == sorted([sha(b'base'), sha(b'doomed')])


def test_retry_with_identical_records_after_a_killed_publish_leaves_one_manifest(store):
    killed_publish(store, 'after_manifest')
    store.publish('p1', 'report', b'doomed', records=evaluation_records(b'doomed'))
    report = store.reindex()
    assert report['manifests'] == 1 and report['issue_count'] == 0

