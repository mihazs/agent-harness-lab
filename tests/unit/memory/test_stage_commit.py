"""Stage commit: output, record and receipt are stored under the exact node key; failures stay diagnostic (synthetic data)."""
import fcntl
import hashlib
import json
import os

import pytest

from agent_harness_lab.memory import commit, reuse
from agent_harness_lab.memory.store import MemoryStore, MemoryStoreError, ReadOnlyStore, StoreBusy

GOOD = {'bytes': True, 'oracle': True, 'bank': True, 'role': True}
STAGE = 'source_status'


def h(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def store(tmp_path):
    return MemoryStore.init(tmp_path / 'store')


def parts(stage='source_status', version='1', inputs=(), config=None):
    return reuse.components(stage, version, inputs, {'project': 'p'}, config or {}, {'role': 'operator'})


def rows(store, project='p1'):
    with store._db() as db:
        return db.execute('SELECT node_key, stage, output, status, sequence FROM stage_records WHERE project_id=? '
                          'ORDER BY sequence', (project,)).fetchall()


def record_digest(store, key, project='p1'):
    return store.stage_rows(project, STAGE, node_key=key)[0]['record']


# ---- commit and lookup -------------------------------------------------------------------------------------------
def test_committed_stage_is_found_under_its_exact_node_key(store):
    p = parts()
    record = commit.commit_stage(store, 'p1', p, b'output-bytes')
    key = reuse.node_key(p)
    assert record == {'node_key': key, 'components': p, 'output': hashlib.sha256(b'output-bytes').hexdigest(),
                      'status': 'complete'}
    assert commit.lookup(store, 'p1', STAGE, key) == record
    assert store.read(record['output']) == b'output-bytes'
    assert rows(store) == [(key, 'source_status', record['output'], 'complete', 1)]


def test_lookup_is_unknown_for_other_keys_and_other_projects(store):
    p = parts()
    commit.commit_stage(store, 'p1', p, b'o')
    assert commit.lookup(store, 'p1', STAGE, reuse.node_key(parts(version='2'))) is None
    assert commit.lookup(store, 'p2', STAGE, reuse.node_key(p)) is None


def test_commit_records_feed_the_planner_and_every_stage_hits(store):
    outputs, ledger = {}, {}
    external = {'source_status': [h('rev-1')], 'harness_inventory': [h('harness-1')]}
    for stage in reuse.order():
        inputs = sorted({*external.get(stage, ()), *(outputs[d] for d in reuse.GRAPH[stage])})
        record = commit.commit_stage(store, 'p1', parts(stage, inputs=inputs), b'out-' + stage.encode())
        outputs[stage] = record['output']
        ledger[stage] = commit.lookup(store, 'p1', stage, record['node_key'])
    reqs = {s: {'stage_version': '1', 'external_inputs': external.get(s, []), 'scope': {'project': 'p'},
                'config': {}, 'policy': {'role': 'operator'}} for s in reuse.GRAPH}
    receipt = reuse.plan(reqs, ledger, {s: GOOD for s in reuse.GRAPH}, source_freshness='verified')
    assert {r['reason'] for b in ('reused', 'executed', 'excluded', 'unknown') for r in receipt[b]} == {'hit_verified'}
    assert receipt['counts'] == {'reused_deterministic': 8, 'executed_deterministic': 0}


def test_recommitting_the_same_stage_is_idempotent_and_writes_nothing(store):
    p = parts()
    first = commit.commit_stage(store, 'p1', p, b'o')
    before = (store.status()['artifacts'], store.status()['object_files'])
    store._hook = lambda step: pytest.fail(f'unexpected durable write step {step}')
    assert commit.commit_stage(store, 'p1', p, b'o') == first
    assert (store.status()['artifacts'], store.status()['object_files']) == before
    assert len(rows(store)) == 1


def test_a_different_output_under_the_same_key_is_refused_and_the_first_stays(store):
    p = parts()
    first = commit.commit_stage(store, 'p1', p, b'one')
    with pytest.raises(MemoryStoreError, match='different output'):
        commit.commit_stage(store, 'p1', p, b'two')
    assert commit.lookup(store, 'p1', STAGE, first['node_key']) == first
    assert len(store.artifacts('p1')['artifacts']) == 2 and len(rows(store)) == 1  # output + record only


@pytest.mark.parametrize('stage', ['candidate_proposal', 'agent_trial', 'not_a_stage'])
def test_nonreplayable_and_unknown_stages_are_never_committed(store, stage):
    with pytest.raises(ValueError, match='not a deterministic stage'):
        commit.commit_stage(store, 'p1', parts(stage), b'o')
    assert store.artifacts('p1')['artifacts'] == []


@pytest.mark.parametrize('mutate', [
    pytest.param(lambda p: p.pop('policy'), id='missing-facet'),
    pytest.param(lambda p: p.update(canonicalizer=2), id='unsupported-canonicalizer'),
    pytest.param(lambda p: p.update(inputs=sorted([h('a'), h('b')], reverse=True)), id='unsorted-inputs'),
    pytest.param(lambda p: p.update(extra=1), id='extra-facet'),
    pytest.param(lambda p: p.update(config={'ratio': 1.5}), id='float-value'),
])
def test_components_that_are_not_what_the_planner_hashes_are_refused(store, mutate):
    p = parts()
    mutate(p)
    with pytest.raises(ValueError):
        commit.commit_stage(store, 'p1', p, b'o')
    assert store.artifacts('p1')['artifacts'] == []


def test_oversize_output_is_refused_and_leaves_no_record(tmp_path):
    small = MemoryStore.init(tmp_path / 'small', max_object_bytes=8)
    with pytest.raises(MemoryStoreError, match='byte bound'):
        commit.commit_stage(small, 'p1', parts(), b'x' * 9)
    assert small.artifacts('p1')['artifacts'] == [] and commit.lookup(small, 'p1', STAGE, reuse.node_key(parts())) is None


def test_empty_output_is_a_real_output_and_unknown_kind_is_refused(store):
    assert commit.commit_stage(store, 'p1', parts(), b'')['output'] == hashlib.sha256(b'').hexdigest()
    with pytest.raises(MemoryStoreError):
        commit.commit_stage(store, 'p1', parts(version='2'), b'o', kind='verdict')
    assert commit.lookup(store, 'p1', STAGE, reuse.node_key(parts(version='2'))) is None


# ---- the one record layout, shared with `ahl analyze --incremental` ------------------------------------------------
def test_the_record_body_is_the_layout_analyze_reads(store):
    p = parts()
    record = commit.commit_stage(store, 'p1', p, b'o')
    [row] = store.stage_rows('p1', STAGE)
    body = json.loads(store.read(row['record']))
    assert body == {'schema': commit.RECORD_SCHEMA, 'stage': STAGE, 'node_key': record['node_key'], 'components': p,
                    'status': 'complete', 'output': record['output'], 'sequence': 1}
    assert row == {'record': hashlib.sha256(reuse.canonical_json(body)).hexdigest(), 'node_key': record['node_key'],
                   'status': 'complete', 'output': record['output'], 'sequence': 1}


def test_sequence_grows_by_one_per_stage_across_commits_and_events(store):
    first, second = parts(), parts(version='2')
    commit.commit_stage(store, 'p1', first, b'a')
    commit.record_event(store, 'p1', second, 'failed', 'timeout')
    commit.commit_stage(store, 'p1', second, b'b')
    commit.commit_stage(store, 'p1', parts('harness_inventory'), b'c')
    commit.commit_stage(store, 'p2', parts(), b'a')
    sequences = lambda project, stage: [r['sequence'] for r in reversed(store.stage_rows(project, stage))]
    assert sequences('p1', STAGE) == [1, 2, 3] and sequences('p1', 'harness_inventory') == [1] and sequences('p2', STAGE) == [1]


def test_a_stage_committed_under_one_key_is_not_found_under_another_stage_name(store):
    record = commit.commit_stage(store, 'p1', parts(), b'o')
    assert commit.lookup(store, 'p1', 'harness_inventory', record['node_key']) is None


# ---- crash, corruption and rebuild -------------------------------------------------------------------------------
def test_stage_killed_before_its_record_is_durable_is_not_reusable_and_can_be_retried(store):
    p, seen = parts(), []

    def hook(step):
        seen.append(step)
        if step == 'after_object' and seen.count('after_object') == 2:  # the record blob is written, its manifest is not
            raise KeyboardInterrupt

    store._hook = hook
    with pytest.raises(KeyboardInterrupt):
        commit.commit_stage(store, 'p1', p, b'o')
    store._hook = None
    key = reuse.node_key(p)
    assert commit.lookup(store, 'p1', STAGE, key) is None and rows(store) == []
    assert commit.commit_stage(store, 'p1', p, b'o')['node_key'] == key
    assert commit.lookup(store, 'p1', STAGE, key)['output'] == hashlib.sha256(b'o').hexdigest()


def test_corrupt_record_is_refused_never_returned_and_never_overwritten(store):
    p = parts()
    commit.commit_stage(store, 'p1', p, b'o')
    key = reuse.node_key(p)
    digest = record_digest(store, key)
    record_file = store.root / 'objects/sha256' / digest[:2] / digest
    record_file.chmod(0o600)
    record_file.write_bytes(b'{}')
    for action in (lambda: commit.lookup(store, 'p1', STAGE, key), lambda: commit.commit_stage(store, 'p1', p, b'o')):
        with pytest.raises(MemoryStoreError, match='integrity'):
            action()
    assert record_file.read_bytes() == b'{}'


def forge(store, key, loose=False, **change):
    """Index a hash-valid record whose body contradicts its own complete row, as a damaged or hand-edited store could."""
    output = store.publish('p1', 'analysis', b'x')
    body = {'schema': commit.RECORD_SCHEMA, 'stage': STAGE, 'node_key': key, 'components': parts(), 'status': 'complete',
            'output': output, 'sequence': 1, **change}
    raw = json.dumps(body, indent=1).encode() if loose else reuse.canonical_json(body)
    row = {'record': hashlib.sha256(raw).hexdigest(), 'stage': STAGE, 'node_key': key, 'status': 'complete',
           'output': output, 'sequence': 1}
    store.publish('p1', 'manifest', raw, dependencies=[output], records=[{'table': 'stage_records', 'row': row}])


@pytest.mark.parametrize('change', [
    pytest.param({'components': parts(version='2')}, id='components-hash-to-another-key'),
    pytest.param({'node_key': h('other')}, id='record-names-another-key'),
    pytest.param({'stage': 'harness_inventory'}, id='record-names-another-stage'),
    pytest.param({'components': parts('harness_inventory')}, id='components-name-another-stage'),
    pytest.param({'status': 'incomplete'}, id='incomplete-status'),
    pytest.param({'output': None}, id='no-output'),
    pytest.param({'output': 'not-a-digest'}, id='malformed-output'),
    pytest.param({'output': h('another output')}, id='output-differs-from-the-row'),
    pytest.param({'sequence': 2}, id='sequence-differs-from-the-row'),
    pytest.param({'sequence': True}, id='boolean-sequence'),
    pytest.param({'sequence': None}, id='null-sequence'),
    pytest.param({'schema': 'ahl-stage-record-v2'}, id='unknown-schema'),
])
def test_forged_record_with_a_valid_hash_is_refused_instead_of_returned(store, change):
    key = reuse.node_key(parts())
    forge(store, key, **change)
    with pytest.raises(MemoryStoreError, match='verification'):
        commit.lookup(store, 'p1', STAGE, key)


def test_record_that_is_not_canonical_json_is_refused_even_when_its_content_is_valid(store):
    key = reuse.node_key(parts())
    forge(store, key, loose=True)
    with pytest.raises(MemoryStoreError, match='verification'):
        commit.lookup(store, 'p1', STAGE, key)


def test_committed_stages_and_events_survive_an_index_rebuild(store):
    p = parts()
    record = commit.commit_stage(store, 'p1', p, b'o')
    commit.record_event(store, 'p1', parts(version='2'), 'failed', 'parser_error')
    before = rows(store)
    os.unlink(store.root / 'index.sqlite')
    assert store.reindex()['rebuilt'] is True
    assert commit.lookup(store, 'p1', STAGE, record['node_key']) == record
    assert rows(store) == before and store.verify()['ok']


# ---- diagnostic events --------------------------------------------------------------------------------------------
@pytest.mark.parametrize('event', ['failed', 'interrupted'])
def test_failed_or_interrupted_stage_keeps_a_typed_event_and_is_never_reusable(store, event):
    p = parts()
    commit.record_event(store, 'p1', p, event, 'timeout')
    key = reuse.node_key(p)
    [(recorded_key, stage, output, status, sequence)] = rows(store)
    assert (recorded_key, stage, output, status, sequence) == (key, 'source_status', None, 'incomplete', 1)
    assert commit.lookup(store, 'p1', STAGE, key) is None
    reqs = {s: {'stage_version': '1', 'external_inputs': [], 'scope': {'project': 'p'}, 'config': {},
                'policy': {'role': 'operator'}} for s in reuse.GRAPH}
    record = json.loads(store.read(store.stage_rows('p1', STAGE)[0]['record']))
    assert record['event'] == event and record['code'] == 'timeout' and record['output'] is None
    receipt = reuse.plan(reqs, {'source_status': {**record, 'node_key': key}}, {s: GOOD for s in reuse.GRAPH},
                         source_freshness='verified')
    assert {r['stage']: r['reason'] for r in receipt['executed']}['source_status'] == 'miss_no_prior'


def test_every_event_is_its_own_record_in_sequence_even_when_identical(store):
    p = parts()
    for event, code in (('failed', 'timeout'), ('failed', 'timeout'), ('interrupted', 'timeout'), ('failed', 'parser_error')):
        commit.record_event(store, 'p1', p, event, code)
    assert [(r[3], r[4]) for r in rows(store)] == [('incomplete', n) for n in (1, 2, 3, 4)]


def test_an_event_never_blocks_or_replaces_a_later_complete_commit(store):
    p = parts()
    commit.record_event(store, 'p1', p, 'interrupted', 'killed')
    record = commit.commit_stage(store, 'p1', p, b'o')
    assert commit.lookup(store, 'p1', STAGE, record['node_key']) == record
    assert [(r[3], r[4]) for r in rows(store)] == [('incomplete', 1), ('complete', 2)]


@pytest.mark.parametrize('event, code', [
    pytest.param('succeeded', 'timeout', id='unknown-event'),
    pytest.param('', 'timeout', id='empty-event'),
    pytest.param('failed', 'free text with spaces and /private/path', id='free-text-code'),
    pytest.param('failed', '', id='empty-code'),
    pytest.param('failed', 'x' * 129, id='oversize-code'),
])
def test_events_take_only_a_known_kind_and_a_short_identifier(store, event, code):
    with pytest.raises(ValueError):
        commit.record_event(store, 'p1', parts(), event, code)
    assert store.artifacts('p1')['artifacts'] == []


def test_events_for_nonreplayable_stages_are_refused(store):
    with pytest.raises(ValueError, match='not a deterministic stage'):
        commit.record_event(store, 'p1', parts('agent_trial'), 'failed', 'timeout')


# ---- per-project lock and read-only stores -------------------------------------------------------------------------
def commit_lock_name(store, project):
    with store.project_lock(project):
        return next(p.name for p in (store.root / 'locks').iterdir() if p.name.startswith('project-'))


def test_a_busy_project_lock_refuses_commits_within_the_bound_and_releases_afterwards(store):
    store.busy_seconds = 0.2
    descriptor = os.open(store.root / 'locks' / commit_lock_name(store, 'p1'), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    try:
        with pytest.raises(StoreBusy):
            commit.commit_stage(store, 'p1', parts(), b'o')
        with pytest.raises(StoreBusy):
            commit.record_event(store, 'p1', parts(), 'failed', 'timeout')
        assert store.artifacts('p1')['artifacts'] == [] and store.verify()['ok']
    finally:
        os.close(descriptor)
    assert commit.commit_stage(store, 'p1', parts(), b'o')['status'] == 'complete'


def test_a_failed_commit_releases_the_project_lock(store):
    p = parts()
    commit.commit_stage(store, 'p1', p, b'one')
    with pytest.raises(MemoryStoreError):
        commit.commit_stage(store, 'p1', p, b'two')
    store.busy_seconds = 0.2
    assert commit.commit_stage(store, 'p1', p, b'one')['status'] == 'complete'


def test_project_lock_files_are_bounded_regardless_of_project_count(store):
    for number in range(300):
        with store.project_lock(f'project-{number}'):
            pass
    assert len([p for p in (store.root / 'locks').iterdir() if p.name.startswith('project-')]) <= 256


def test_a_read_only_store_refuses_every_commit(store):
    path = store.root / 'store.json'
    meta = json.loads(path.read_text())
    meta['format_version'] = 2
    path.write_text(json.dumps(meta))
    reopened = MemoryStore.open(store.root)
    for action in (lambda: commit.commit_stage(reopened, 'p1', parts(), b'o'),
                   lambda: commit.record_event(reopened, 'p1', parts(), 'failed', 'timeout'),
                   lambda: commit.commit_receipt(reopened, 'p1', receipt_for_everything_unknown())):
        with pytest.raises(ReadOnlyStore):
            action()


# ---- receipts -----------------------------------------------------------------------------------------------------
def receipt_for_everything_unknown():
    reqs = {s: {'stage_version': '1', 'external_inputs': [], 'scope': {}, 'config': {}, 'policy': {}} for s in reuse.GRAPH}
    return reuse.plan(reqs)


def test_receipt_is_stored_as_canonical_json_idempotently(store):
    receipt = receipt_for_everything_unknown()
    digest = commit.commit_receipt(store, 'p1', receipt)
    assert digest == hashlib.sha256(reuse.canonical_json(receipt)).hexdigest()
    assert json.loads(store.read(digest)) == receipt
    assert commit.commit_receipt(store, 'p1', receipt) == digest
    assert [(a['digest'], a['kind']) for a in store.artifacts('p1')['artifacts']] == [(digest, 'report')]


def drop_unknown(receipt):
    receipt['unknown'] = receipt['unknown'][1:]


@pytest.mark.parametrize('mutate', [
    pytest.param(lambda r: r.update(publication_allowed=True), id='publication-allowed'),
    pytest.param(lambda r: r.update(reused_live_trials=1), id='reused-live-trial'),
    pytest.param(lambda r: r.update(reused_live_trials=False), id='bool-live-trials'),
    pytest.param(lambda r: r.update(schema='ahl-stage-reuse-v2'), id='unknown-schema'),
    pytest.param(drop_unknown, id='unknown-node-omitted'),
    pytest.param(lambda r: r['unknown'].append(dict(r['unknown'][0])), id='node-listed-twice'),
    pytest.param(lambda r: r.pop('excluded'), id='missing-bucket'),
])
def test_receipts_that_hide_nodes_or_break_the_frozen_invariants_are_refused(store, mutate):
    receipt = receipt_for_everything_unknown()
    mutate(receipt)
    with pytest.raises(ValueError):
        commit.commit_receipt(store, 'p1', receipt)
    assert store.artifacts('p1')['artifacts'] == []


def test_record_missing_from_the_object_store_is_reported_not_treated_as_absent(store):
    p = parts()
    commit.commit_stage(store, 'p1', p, b'o')
    digest = record_digest(store, reuse.node_key(p))
    os.unlink(store.root / 'objects/sha256' / digest[:2] / digest)
    with pytest.raises(MemoryStoreError, match='missing'):
        commit.lookup(store, 'p1', STAGE, reuse.node_key(p))


def test_output_that_is_not_bytes_is_refused(store):
    with pytest.raises(ValueError, match='bytes'):
        commit.commit_stage(store, 'p1', parts(), 'text')
    assert store.artifacts('p1')['artifacts'] == []
