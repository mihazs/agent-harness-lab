"""Optional unlimited journal sizes preserve strict storage and default policies."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from agent_harness_lab.evaluation.integrity import digest,read_regular,tree,tree_digest
from agent_harness_lab.private_journal import MAX_EVENTS,MAX_METADATA,PrivateJournal,encoded,record,record_bytes


def transition(previous,kind,state,previous_kind=None):
    return kind==state=='observed'


class PrivateJournalSizeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)/'journal'

    def journal(self,**limits):
        store=PrivateJournal(self.root,'synthetic-journal-v1',transition,**limits)
        with store.locked(create=True):pass
        return store

    def case(self,store):
        case=store.case('a'*64);(case/'events').mkdir(parents=True)
        return case

    def test_unlimited_records_exceed_old_bytes_but_keep_strict_json_and_safe_reads(self):
        raw=encoded(dict(text='x'*(MAX_METADATA+1)))
        path=Path(self.root.parent)/'metadata.json';path.write_bytes(raw)
        for read in (lambda:record_bytes(raw),lambda:record(path),lambda:read_regular(path,MAX_METADATA)):
            with self.assertRaises(ValueError):read()
        expected=(json.loads(raw),hashlib.sha256(raw).hexdigest())
        self.assertEqual(record_bytes(raw,None),expected);self.assertEqual(record(path,None),expected)
        self.assertEqual(read_regular(path,None),raw)
        for invalid in (b'{"a":1,"a":2}',b'{"value":NaN}',b'[]'):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):record_bytes(invalid,None)
        linked=path.with_name('linked.json');linked.symlink_to(path)
        with self.assertRaises(ValueError):record(linked,None)
        with self.assertRaises(ValueError):read_regular(path.parent,None)

    def test_unlimited_metadata_roundtrips_large_event_without_expanding_default(self):
        store=self.journal(max_metadata=None);case=self.case(store);events=[]
        payload=dict(text='x'*(MAX_METADATA+1))
        store.append(case,events,'observed','observed',payload)
        self.assertEqual(store.history(case),events)
        limited=PrivateJournal(self.root,store.format,transition)
        with self.assertRaises(ValueError):limited.history(case)
        with self.assertRaises(ValueError):limited.append(case,events,'observed','observed',payload)
        self.assertEqual(len(list((case/'events').iterdir())),1)

    def test_default_operation_count_remains_limited_and_optin_allows_next_event(self):
        store=self.journal();case=self.case(store);events=[]
        for _ in range(MAX_EVENTS):store.append(case,events,'observed','observed',{})
        with self.assertRaises(ValueError):store.append(case,events,'observed','observed',{})
        unlimited=PrivateJournal(self.root,store.format,transition,max_events=None)
        unlimited.append(case,events,'observed','observed',{})
        self.assertEqual(len(unlimited.history(case)),MAX_EVENTS+1)

    def test_unlimited_entries_preserve_canonical_chain_after_four_digit_indices(self):
        store=self.journal(max_events=None,max_entries=None);case=self.case(store);previous=None
        for index in range(10001):
            event=dict(schema_version=1,index=index,previous_sha256=previous,kind='observed',state='observed',
                payload={},recorded_at='synthetic-fixed-time')
            raw=encoded(event);(case/'events'/f'{index:04d}.json').write_bytes(raw)
            previous=hashlib.sha256(raw).hexdigest()
        events=store.history(case)
        self.assertEqual((len(events),events[-1][0]['index']),(10001,10000))
        store.append(case,events,'observed','observed',{})
        self.assertEqual(store.history(case),events)
        limited=PrivateJournal(self.root,store.format,transition)
        with self.assertRaises(ValueError):limited.history(case)
        (case/'events'/'10001.json').rename(case/'events'/'010001.json')
        with self.assertRaises(ValueError):store.history(case)

    def test_unlimited_case_listing_retains_path_and_directory_validation(self):
        store=self.journal();folder=self.root/'cases';folder.mkdir()
        expected=[f'{index:064x}' for index in range(129)]
        for name in expected:(folder/name).mkdir()
        with self.assertRaises(ValueError):store.case_ids(128)
        self.assertEqual(store.case_ids(None),expected)
        unknown=folder/'unknown';unknown.mkdir()
        with self.assertRaises(ValueError):store.case_ids(None)
        unknown.rmdir();(folder/('f'*64)).write_text('not a case directory')
        with self.assertRaises(ValueError):store.case_ids(None)

    def test_custom_sizes_and_invalid_policy_values(self):
        for name in ('max_events','max_metadata','max_entries'):
            for value in (0,-1,True,'unlimited'):
                with self.subTest(name=name,value=value),self.assertRaises(ValueError):
                    PrivateJournal(self.root,'synthetic-journal-v1',transition,**{name:value})
        store=self.journal(max_events=1,max_metadata=1024,max_entries=1);case=self.case(store);events=[]
        store.append(case,events,'observed','observed',{})
        with self.assertRaises(ValueError):store.append(case,events,'observed','observed',{})
        (case/'events'/'.extra').write_text('ignored but counted')
        with self.assertRaises(ValueError):store.history(case)

    def test_tree_optin_exceeds_previous_bytes_files_and_depth_without_changing_defaults(self):
        root=self.root.parent/'inputs';root.mkdir()
        large=root/'large';large.write_bytes(b'x'*(10*1024*1024+1))
        for index in range(1000):(root/f'file-{index}').write_bytes(b'')
        nested=root
        for _ in range(65):nested=nested/'directory';nested.mkdir()
        (nested/'leaf').write_bytes(b'leaf')
        with self.assertRaises(ValueError):tree(root)
        limits=dict(max_files=None,max_bytes=None,max_entries=None,max_depth=None)
        files=tree(root,**limits)
        self.assertEqual(len(files),1002)
        self.assertEqual(digest(root,**limits),tree_digest(files))
        for key,value in (('max_files',1000),('max_bytes',10*1024*1024),('max_entries',1000),('max_depth',64)):
            with self.subTest(key=key),self.assertRaises(ValueError):tree(root,**dict(limits,**{key:value}))
        (root/'linked').symlink_to(large)
        with self.assertRaises(ValueError):tree(root,**limits)


if __name__=='__main__':unittest.main()
