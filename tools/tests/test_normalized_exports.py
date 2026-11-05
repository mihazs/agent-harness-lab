"""Synthetic local export readbacks; no network, credentials or outcome authority."""
from contextlib import ExitStack
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

import agent_harness_lab
from agent_harness_lab.sources import snapshot
from agent_harness_lab.sources.execution import ExecutionMetadata
from agent_harness_lab.sources.schema import digest
if __package__:
    from . import test_source_dispatch as fixtures
else:
    import test_source_dispatch as fixtures


class NormalizedExportTests(unittest.TestCase):
    def setUp(self):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:
            self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()),
                            'normalized export checks imported the wrong package layout')
        self.f=fixtures.SourceDispatchTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.cache=self.f.root/'cache.json'

    def seed(self):
        code,_=self.f.invoke('import','--cache',str(self.cache)); self.assertEqual(code,0)
        before=self.cache.read_bytes()
        newer=replace(self.f.record,revision_sha256=digest('second-revision'),content_sha256=digest('second-content'))
        self.f.data.write_text(json.dumps([newer.to_dict()]))
        return before

    def mutate_during_read(self,change,at=1):
        # The capture reads the payload whole; the readback compares it chunk by chunk.
        original,compare=snapshot.read_regular,snapshot.regular_equal; reads=[]
        def observed(path,result):
            reads.append(Path(path))
            if Path(path)==self.f.data and reads.count(self.f.data)==at: change()
            return result
        control=ExitStack()
        control.enter_context(patch.object(snapshot,'read_regular',side_effect=lambda path,limit:observed(path,original(path,limit))))
        control.enter_context(patch.object(snapshot,'regular_equal',
            side_effect=lambda path,expected,limit:observed(path,compare(path,expected,limit))))
        return reads,control

    def test_payload_change_after_capture_cannot_advance_checkpoint(self):
        before=self.seed()
        changed=replace(self.f.record,revision_sha256=digest('third-revision'),content_sha256=digest('third-content'))
        reads,control=self.mutate_during_read(lambda:self.f.data.write_text(json.dumps([changed.to_dict()])))
        with control: code,_=self.f.invoke('import','--cache',str(self.cache))
        self.assertEqual(code,2); self.assertEqual(self.cache.read_bytes(),before)
        self.assertEqual(reads,[self.f.data,self.f.data])

    def test_disabled_source_is_rejected_before_readback_payload_access(self):
        before=self.seed()
        reads,control=self.mutate_during_read(lambda:self.f.declare(replace(self.f.source,enabled=False)))
        with control: code,_=self.f.invoke('import','--cache',str(self.cache))
        self.assertEqual(code,2); self.assertEqual(self.cache.read_bytes(),before)
        self.assertEqual(reads,[self.f.data])

    def test_foreign_scope_is_rejected_before_readback_payload_access(self):
        before=self.seed()
        reads,control=self.mutate_during_read(lambda:self.f.declare(replace(self.f.source,project_sha256=digest('foreign'))))
        with control: code,_=self.f.invoke('import','--cache',str(self.cache))
        self.assertEqual(code,2); self.assertEqual(self.cache.read_bytes(),before)
        self.assertEqual(reads,[self.f.data])

    def test_disable_during_payload_readback_is_caught_by_final_declaration_check(self):
        before=self.seed()
        reads,control=self.mutate_during_read(lambda:self.f.declare(replace(self.f.source,enabled=False)),at=2)
        with control: code,_=self.f.invoke('import','--cache',str(self.cache))
        self.assertEqual(code,2); self.assertEqual(self.cache.read_bytes(),before)
        self.assertEqual(reads,[self.f.data,self.f.data])

    def test_selector_change_is_rejected_before_reading_even_an_identical_alternative(self):
        before=self.seed(); alternative=self.f.root/'alternative.json'
        alternative.write_bytes(self.f.data.read_bytes())
        reads,control=self.mutate_during_read(lambda:self.f.declare(data='alternative.json'))
        with control: code,_=self.f.invoke('import','--cache',str(self.cache))
        self.assertEqual(code,2); self.assertEqual(self.cache.read_bytes(),before)
        self.assertEqual(reads,[self.f.data])

    def test_removed_linked_partial_or_oversized_payload_does_not_commit(self):
        original=self.f.data.read_bytes()
        for kind in ('removed','linked','partial','oversized'):
            with self.subTest(kind=kind):
                if self.f.data.is_symlink() or self.f.data.exists(): self.f.data.unlink()
                self.f.data.write_bytes(original)
                if self.cache.exists(): self.cache.unlink()
                before=self.seed()
                def change():
                    self.f.data.unlink()
                    if kind=='linked': self.f.data.symlink_to(self.f.manifest)
                    elif kind=='partial': self.f.data.write_bytes(b'{"partial":')
                    elif kind=='oversized': self.f.data.write_bytes(b' '*(8*1024*1024+1))
                _,control=self.mutate_during_read(change)
                with control: code,_=self.f.invoke('import','--cache',str(self.cache))
                self.assertEqual(code,2); self.assertEqual(self.cache.read_bytes(),before)

    def test_unchanged_readbacks_preserve_v1_v2_v3_hashes_and_idempotent_cursors(self):
        for version in ('v1','v2','v3'):
            with self.subTest(version=version):
                self.f.source=replace(self.f.source,version=version); self.f.declare()
                rows=[self.f.record.to_dict()]
                if version!='v1':
                    rows=dict(schema_version=int(version[1:]),records=rows,
                              execution_metadata=ExecutionMetadata(()).to_dict() if version=='v2' else None)
                    if version=='v3': rows['evidence_graph']=None
                self.f.data.write_text(json.dumps(rows)); expected=hashlib.sha256(self.f.data.read_bytes()).hexdigest()
                reads,control=self.mutate_during_read(lambda:None)
                with control,patch.object(socket,'socket',side_effect=AssertionError('no network')):
                    code,raw=self.f.invoke('preview')
                self.assertEqual(code,0); preview=json.loads(raw)
                self.assertEqual(preview['snapshot_sha256'],expected)
                self.assertEqual(preview['capture_completeness'],'unknown')
                self.assertEqual(reads,[self.f.data,self.f.data])
                cache=self.f.root/(version+'-cache.json')
                code,raw=self.f.invoke('import','--cache',str(cache)); self.assertEqual(code,0)
                first=json.loads(raw)
                code,raw=self.f.invoke('import','--cache',str(cache)); self.assertEqual(code,0)
                second=json.loads(raw)
                self.assertEqual(second['imported'],0); self.assertEqual(second['skipped'],1)
                self.assertEqual(first['cursor_after_sha256'],second['cursor_after_sha256'])

    def test_mtime_or_declaration_format_only_changes_do_not_invent_snapshot_revisions(self):
        expected=hashlib.sha256(self.f.data.read_bytes()).hexdigest()
        declaration=json.loads(self.f.manifest.read_text())
        def change():
            self.f.data.touch(); self.f.manifest.write_text(json.dumps(declaration,indent=2))
        _,control=self.mutate_during_read(change)
        with control: code,raw=self.f.invoke('preview')
        self.assertEqual(code,0); self.assertEqual(json.loads(raw)['snapshot_sha256'],expected)


if __name__=='__main__': unittest.main()
