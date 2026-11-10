"""Synthetic local export readbacks; no network, credentials or outcome authority."""
from contextlib import ExitStack
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import socket
from unittest.mock import patch

import pytest

import agent_harness_lab
from agent_harness_lab.sources import snapshot
from agent_harness_lab.sources.execution import ExecutionMetadata
from agent_harness_lab.sources.schema import digest
from tests.integration.sources import test_source_dispatch as fixtures


class ExportReadback:
    """A declared synthetic local export (from the source dispatch fixture) and its import cache."""

    def __init__(self, f):
        self.f=f
        self.cache=self.f.root/'cache.json'

    def seed(self):
        code,_=self.f.invoke('import','--cache',str(self.cache)); assert code == 0
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


@pytest.fixture
def ex():
    expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
    if expected:
        assert Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()), 'normalized export checks imported the wrong package layout'
    f=fixtures.SourceDispatchTests(); f.setUp()
    try:
        yield ExportReadback(f)
    finally:
        f.doCleanups()


def test_payload_change_after_capture_cannot_advance_checkpoint(ex):
    before=ex.seed()
    changed=replace(ex.f.record,revision_sha256=digest('third-revision'),content_sha256=digest('third-content'))
    reads,control=ex.mutate_during_read(lambda:ex.f.data.write_text(json.dumps([changed.to_dict()])))
    with control: code,_=ex.f.invoke('import','--cache',str(ex.cache))
    assert code == 2; assert ex.cache.read_bytes() == before
    assert reads == [ex.f.data,ex.f.data]


def test_disabled_source_is_rejected_before_readback_payload_access(ex):
    before=ex.seed()
    reads,control=ex.mutate_during_read(lambda:ex.f.declare(replace(ex.f.source,enabled=False)))
    with control: code,_=ex.f.invoke('import','--cache',str(ex.cache))
    assert code == 2; assert ex.cache.read_bytes() == before
    assert reads == [ex.f.data]


def test_foreign_scope_is_rejected_before_readback_payload_access(ex):
    before=ex.seed()
    reads,control=ex.mutate_during_read(lambda:ex.f.declare(replace(ex.f.source,project_sha256=digest('foreign'))))
    with control: code,_=ex.f.invoke('import','--cache',str(ex.cache))
    assert code == 2; assert ex.cache.read_bytes() == before
    assert reads == [ex.f.data]


def test_disable_during_payload_readback_is_caught_by_final_declaration_check(ex):
    before=ex.seed()
    reads,control=ex.mutate_during_read(lambda:ex.f.declare(replace(ex.f.source,enabled=False)),at=2)
    with control: code,_=ex.f.invoke('import','--cache',str(ex.cache))
    assert code == 2; assert ex.cache.read_bytes() == before
    assert reads == [ex.f.data,ex.f.data]


def test_selector_change_is_rejected_before_reading_even_an_identical_alternative(ex):
    before=ex.seed(); alternative=ex.f.root/'alternative.json'
    alternative.write_bytes(ex.f.data.read_bytes())
    reads,control=ex.mutate_during_read(lambda:ex.f.declare(data='alternative.json'))
    with control: code,_=ex.f.invoke('import','--cache',str(ex.cache))
    assert code == 2; assert ex.cache.read_bytes() == before
    assert reads == [ex.f.data]


@pytest.mark.parametrize('kind',('removed','linked','partial','oversized'))
def test_removed_linked_partial_or_oversized_payload_does_not_commit(ex,kind):
    before=ex.seed()
    def change():
        ex.f.data.unlink()
        if kind=='linked': ex.f.data.symlink_to(ex.f.manifest)
        elif kind=='partial': ex.f.data.write_bytes(b'{"partial":')
        elif kind=='oversized': ex.f.data.write_bytes(b' '*(8*1024*1024+1))
    _,control=ex.mutate_during_read(change)
    with control: code,_=ex.f.invoke('import','--cache',str(ex.cache))
    assert code == 2; assert ex.cache.read_bytes() == before


@pytest.mark.parametrize('version',('v1','v2','v3'))
def test_unchanged_readbacks_preserve_v1_v2_v3_hashes_and_idempotent_cursors(ex,version):
    ex.f.source=replace(ex.f.source,version=version); ex.f.declare()
    rows=[ex.f.record.to_dict()]
    if version!='v1':
        rows=dict(schema_version=int(version[1:]),records=rows,
                  execution_metadata=ExecutionMetadata(()).to_dict() if version=='v2' else None)
        if version=='v3': rows['evidence_graph']=None
    ex.f.data.write_text(json.dumps(rows)); expected=hashlib.sha256(ex.f.data.read_bytes()).hexdigest()
    reads,control=ex.mutate_during_read(lambda:None)
    with control,patch.object(socket,'socket',side_effect=AssertionError('no network')):
        code,raw=ex.f.invoke('preview')
    assert code == 0; preview=json.loads(raw)
    assert preview['snapshot_sha256'] == expected
    assert preview['capture_completeness'] == 'unknown'
    assert reads == [ex.f.data,ex.f.data]
    cache=ex.f.root/(version+'-cache.json')
    code,raw=ex.f.invoke('import','--cache',str(cache)); assert code == 0
    first=json.loads(raw)
    code,raw=ex.f.invoke('import','--cache',str(cache)); assert code == 0
    second=json.loads(raw)
    assert second['imported'] == 0; assert second['skipped'] == 1
    assert first['cursor_after_sha256'] == second['cursor_after_sha256']


def test_mtime_or_declaration_format_only_changes_do_not_invent_snapshot_revisions(ex):
    expected=hashlib.sha256(ex.f.data.read_bytes()).hexdigest()
    declaration=json.loads(ex.f.manifest.read_text())
    def change():
        ex.f.data.touch(); ex.f.manifest.write_text(json.dumps(declaration,indent=2))
    _,control=ex.mutate_during_read(change)
    with control: code,raw=ex.f.invoke('preview')
    assert code == 0; assert json.loads(raw)['snapshot_sha256'] == expected
