"""Stage records in the memory index: lookup order, rebuild from manifests, upgrade of an older index, constraints."""
import hashlib
import json
import sqlite3

import pytest

from agent_harness_lab.memory import schema
from agent_harness_lab.memory.store import MemoryStore, MemoryStoreError

STAGE = 'diagnostic_process_mining'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def store(tmp_path):
    return MemoryStore.init(tmp_path / 'store')


def commit(store, key, sequence, *, status='complete', project='p1', stage=STAGE):
    """Publish one output and one stage record for a node key; returns the record digest."""
    output = store.publish(project, 'analysis', f'out-{key}-{sequence}'.encode()) if status == 'complete' else None
    raw = json.dumps({'node_key': key, 'sequence': sequence, 'status': status}).encode()
    row = {'record': sha(raw), 'stage': stage, 'node_key': key, 'status': status, 'output': output, 'sequence': sequence}
    store.publish(project, 'manifest', raw, dependencies=[output] if output else [], records=[{'table': 'stage_records', 'row': row}])
    return row['record']


def test_rows_come_newest_first_and_filter_by_key_and_status(store):
    a, b = sha(b'a'), sha(b'b')
    commit(store, a, 1)
    commit(store, b, 2)
    commit(store, a, 3, status='incomplete')
    assert [(r['node_key'], r['sequence']) for r in store.stage_rows('p1', STAGE)] == [(a, 3), (b, 2), (a, 1)]
    assert [r['sequence'] for r in store.stage_rows('p1', STAGE, status='complete')] == [2, 1]
    assert [r['sequence'] for r in store.stage_rows('p1', STAGE, node_key=a)] == [3, 1]
    assert [r['sequence'] for r in store.stage_rows('p1', STAGE, node_key=a, status='complete', limit=1)] == [1]
    assert store.stage_rows('other-project', STAGE) == [] and store.stage_rows('p1', 'another_stage') == []


def test_rows_are_rebuilt_from_manifests(store):
    key = sha(b'k')
    record = commit(store, key, 1)
    before = store.stage_rows('p1', STAGE)
    assert store.reindex()['rebuilt'] and store.verify()['ok']
    assert store.stage_rows('p1', STAGE) == before and before[0]['record'] == record


def test_a_complete_record_must_name_an_output(store):
    raw = b'record'
    row = {'record': sha(raw), 'stage': STAGE, 'node_key': sha(b'k'), 'status': 'complete', 'output': None, 'sequence': 1}
    with pytest.raises(MemoryStoreError, match='rejected'):
        store.publish('p1', 'manifest', raw, records=[{'table': 'stage_records', 'row': row}])
    assert store.stage_rows('p1', STAGE) == []


def test_a_record_must_describe_its_own_artifact_and_a_stored_output(store):
    raw = b'record'
    row = {'record': sha(b'something else'), 'stage': STAGE, 'node_key': sha(b'k'), 'status': 'incomplete', 'output': None, 'sequence': 1}
    with pytest.raises(MemoryStoreError, match='unknown artifact'):
        store.publish('p1', 'manifest', raw, records=[{'table': 'stage_records', 'row': row}])
    row = dict(row, record=sha(raw), status='complete', output=sha(b'never stored'))
    with pytest.raises(MemoryStoreError, match='unknown artifact'):
        store.publish('p1', 'manifest', raw, records=[{'table': 'stage_records', 'row': row}])


def test_an_index_from_before_stage_records_reads_empty_and_upgrades_on_first_write(store):
    path = store.root / 'index.sqlite'
    for suffix in ('', '-wal', '-shm'):
        (path.parent / f'index.sqlite{suffix}').unlink(missing_ok=True)
    store._create_index()
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute('DROP TABLE stage_records')
    connection.execute('PRAGMA user_version = 1')
    connection.close()
    assert store.stage_rows('p1', STAGE) == []
    commit(store, sha(b'k'), 1)
    assert sqlite3.connect(path).execute('PRAGMA user_version').fetchone()[0] == schema.VERSION
    assert len(store.stage_rows('p1', STAGE)) == 1


def test_per_project_stage_record_ceiling_is_enforced_by_the_schema(monkeypatch):
    monkeypatch.setitem(schema.V2_ROW_LIMITS, 'stage_records', 1)
    connection = sqlite3.connect(':memory:', isolation_level=None)
    schema.migrate(connection, (schema._v1(), schema._v2()))
    connection.execute("INSERT INTO projects VALUES ('p1')")
    for number in range(2):
        connection.execute('INSERT INTO artifacts VALUES (?,?,?,?,?,?)', ('p1', f'{number:064x}', 'f' * 64, 'manifest', 1, 'published'))
    connection.execute("INSERT INTO stage_records VALUES ('p1', ?, 's', ?, 'incomplete', NULL, 1)", (f'{0:064x}', 'a' * 64))
    with pytest.raises(sqlite3.DatabaseError, match='ceiling'):
        connection.execute("INSERT INTO stage_records VALUES ('p1', ?, 's', ?, 'incomplete', NULL, 2)", (f'{1:064x}', 'b' * 64))
