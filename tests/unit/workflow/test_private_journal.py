"""Optional unlimited journal sizes preserve strict storage and default policies."""
import hashlib
import json

import pytest

from agent_harness_lab.evaluation.integrity import digest,read_regular,tree,tree_digest
from agent_harness_lab.private_journal import MAX_EVENTS,MAX_METADATA,PrivateJournal,encoded,record,record_bytes,scratch_dir,scratch_in


def transition(previous,kind,state,previous_kind=None):
    return kind==state=='observed'


@pytest.fixture
def root(tmp_path):
    return tmp_path/'journal'


def journal(root,**limits):
    store=PrivateJournal(root,'synthetic-journal-v1',transition,**limits)
    with store.locked(create=True):pass
    return store


def make_case(store):
    case=store.case('a'*64);(case/'events').mkdir(parents=True)
    return case


def test_unlimited_records_exceed_old_bytes_but_keep_strict_json_and_safe_reads(root):
    raw=encoded(dict(text='x'*(MAX_METADATA+1)))
    path=root.parent/'metadata.json';path.write_bytes(raw)
    for read in (lambda:record_bytes(raw),lambda:record(path),lambda:read_regular(path,MAX_METADATA)):
        with pytest.raises(ValueError):read()
    expected=(json.loads(raw),hashlib.sha256(raw).hexdigest())
    assert record_bytes(raw,None)==expected;assert record(path,None)==expected
    assert read_regular(path,None)==raw
    linked=path.with_name('linked.json');linked.symlink_to(path)
    with pytest.raises(ValueError):record(linked,None)
    with pytest.raises(ValueError):read_regular(path.parent,None)


@pytest.mark.parametrize('invalid',[pytest.param(b'{"a":1,"a":2}',id='duplicate-key'),pytest.param(b'{"value":NaN}',id='nan'),
                                    pytest.param(b'[]',id='array')])
def test_unlimited_records_keep_strict_json(invalid):
    with pytest.raises(ValueError):record_bytes(invalid,None)


def test_unlimited_metadata_roundtrips_large_event_without_expanding_default(root):
    store=journal(root,max_metadata=None);case=make_case(store);events=[]
    payload=dict(text='x'*(MAX_METADATA+1))
    store.append(case,events,'observed','observed',payload)
    assert store.history(case)==events
    limited=PrivateJournal(root,store.format,transition)
    with pytest.raises(ValueError):limited.history(case)
    with pytest.raises(ValueError):limited.append(case,events,'observed','observed',payload)
    assert len(list((case/'events').iterdir()))==1


def test_default_operation_count_remains_limited_and_optin_allows_next_event(root):
    store=journal(root);case=make_case(store);events=[]
    for _ in range(MAX_EVENTS):store.append(case,events,'observed','observed',{})
    with pytest.raises(ValueError):store.append(case,events,'observed','observed',{})
    unlimited=PrivateJournal(root,store.format,transition,max_events=None)
    unlimited.append(case,events,'observed','observed',{})
    assert len(unlimited.history(case))==MAX_EVENTS+1


def test_unlimited_entries_preserve_canonical_chain_after_four_digit_indices(root):
    store=journal(root,max_events=None,max_entries=None);case=make_case(store);previous=None
    for index in range(10001):
        event=dict(schema_version=1,index=index,previous_sha256=previous,kind='observed',state='observed',
            payload={},recorded_at='synthetic-fixed-time')
        raw=encoded(event);(case/'events'/f'{index:04d}.json').write_bytes(raw)
        previous=hashlib.sha256(raw).hexdigest()
    events=store.history(case)
    assert (len(events),events[-1][0]['index'])==(10001,10000)
    store.append(case,events,'observed','observed',{})
    assert store.history(case)==events
    limited=PrivateJournal(root,store.format,transition)
    with pytest.raises(ValueError):limited.history(case)
    (case/'events'/'10001.json').rename(case/'events'/'010001.json')
    with pytest.raises(ValueError):store.history(case)


def test_unlimited_case_listing_retains_path_and_directory_validation(root):
    store=journal(root);folder=root/'cases';folder.mkdir()
    expected=[f'{index:064x}' for index in range(129)]
    for name in expected:(folder/name).mkdir()
    with pytest.raises(ValueError):store.case_ids(128)
    assert store.case_ids(None)==expected
    unknown=folder/'unknown';unknown.mkdir()
    with pytest.raises(ValueError):store.case_ids(None)
    unknown.rmdir();(folder/('f'*64)).write_text('not a case directory')
    with pytest.raises(ValueError):store.case_ids(None)


@pytest.mark.parametrize('name,value',[pytest.param(name,value,id=f'{name}-{value!r}')
    for name in ('max_events','max_metadata','max_entries') for value in (0,-1,True,'unlimited')])
def test_invalid_policy_values(root,name,value):
    with pytest.raises(ValueError):
        PrivateJournal(root,'synthetic-journal-v1',transition,**{name:value})


def test_custom_sizes(root):
    store=journal(root,max_events=1,max_metadata=1024,max_entries=1);case=make_case(store);events=[]
    store.append(case,events,'observed','observed',{})
    with pytest.raises(ValueError):store.append(case,events,'observed','observed',{})
    (case/'events'/'.extra').write_text('ignored but counted')
    with pytest.raises(ValueError):store.history(case)


UNLIMITED=dict(max_files=None,max_bytes=None,max_entries=None,max_depth=None)


def build_inputs(root):
    """A tree over the default bytes, file, entry and depth bounds."""
    root.mkdir()
    large=root/'large';large.write_bytes(b'x'*(10*1024*1024+1))
    for index in range(1000):(root/f'file-{index}').write_bytes(b'')
    nested=root
    for _ in range(65):nested=nested/'directory';nested.mkdir()
    (nested/'leaf').write_bytes(b'leaf')
    return root


@pytest.fixture(scope='module')
def oversized_inputs(tmp_path_factory):
    """Shared by cases that only read the tree."""
    return build_inputs(tmp_path_factory.mktemp('journal-tree')/'inputs')


def test_tree_optin_exceeds_previous_bytes_files_and_depth_without_changing_defaults(root):
    inputs=build_inputs(root.parent/'inputs')
    with pytest.raises(ValueError):tree(inputs)
    files=tree(inputs,**UNLIMITED)
    assert len(files)==1002
    assert digest(inputs,**UNLIMITED)==tree_digest(files)
    (inputs/'linked').symlink_to(inputs/'large')
    with pytest.raises(ValueError):tree(inputs,**UNLIMITED)


@pytest.mark.parametrize('key,value',[pytest.param(key,value,id=key) for key,value in
    (('max_files',1000),('max_bytes',10*1024*1024),('max_entries',1000),('max_depth',64))])
def test_each_previous_tree_bound_still_applies_when_set(oversized_inputs,key,value):
    with pytest.raises(ValueError):tree(oversized_inputs,**dict(UNLIMITED,**{key:value}))


def test_validation_scratch_defaults_to_the_caller_choice_and_restores_after_nesting(tmp_path):
    outer,inner=tmp_path/'outer',tmp_path/'inner'
    assert scratch_dir('default')=='default'
    with scratch_in(outer):
        assert scratch_dir('default')==outer
        with scratch_in(inner):assert scratch_dir('default')==inner
        assert scratch_dir('default')==outer
    assert scratch_dir('default')=='default'


def test_validation_scratch_is_restored_when_the_guarded_block_raises(tmp_path):
    with pytest.raises(RuntimeError),scratch_in(tmp_path):raise RuntimeError('synthetic failure')
    assert scratch_dir(None) is None
