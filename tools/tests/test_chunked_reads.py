"""Synthetic files: hash, size and compare reads hold one chunk yet match whole reads exactly."""
from contextlib import contextmanager
import hashlib
import json
import os
import random
import time
import tracemalloc
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import integrity
from agent_harness_lab.evaluation.integrity import read_regular, regular_equal, regular_sha256, regular_size
if __package__:
    from . import test_source_store as fixtures
else:
    import test_source_store as fixtures

MiB = 1024*1024
CHUNK = MiB  # _regular_chunks reads at most this much per step


def outcome(call):
    try: return 'value', call()
    except Exception as error: return type(error).__name__, str(error)


SIZES = (0, 1, CHUNK-1, CHUNK, CHUNK+1, 3*CHUNK+5)
LIMITS = (('none', lambda size: None), ('size', lambda size: size), ('size+1', lambda size: size+1),
          ('size-1', lambda size: size-1), ('chunk', lambda size: CHUNK), ('chunk-1', lambda size: CHUNK-1))
BOUNDARIES = [pytest.param(size, limit(size), id=f'{size}-{label}') for size in SIZES for label, limit in LIMITS
              if limit(size) is None or limit(size) >= 0]
UNSAFE = ('link', 'parent/inner', 'linked', 'folder', 'fifo', 'missing')
GROWING = [pytest.param(lambda p: hashlib.sha256(read_regular(p, CHUNK+10)).hexdigest(), id='whole-read'),
           pytest.param(lambda p: regular_sha256(p, CHUNK+10), id='sha256'),
           pytest.param(lambda p: regular_size(p, CHUNK+10), id='size'),
           pytest.param(lambda p: regular_equal(p, b'', CHUNK+10), id='equal')]
CACHE_STATES = ('same', 'appended', 'broken', 'missing')
# Each lineage cache change, with the error a capture that sees it must raise.
CAPTURE_CHANGES = [pytest.param('appended', 'source cache changed during lineage capture', id='appended'),
                   pytest.param('broken', 'invalid metadata cache; preserved for inspection', id='broken')]


def boundary_bytes(size):
    """The bytes the shared seeded generator yields for this size when every size is drawn in order."""
    rng = random.Random(221)
    for drawn in SIZES:
        raw = rng.randbytes(drawn)
        if drawn == size: return raw


def peak(call):
    tracemalloc.start()
    try: return call(), tracemalloc.get_traced_memory()[1]
    finally: tracemalloc.stop()


@pytest.fixture
def large(tmp_path):
    path = tmp_path/'large.bin'
    with path.open('wb') as stream:
        for index in range(48): stream.write(random.Random(index).randbytes(MiB))
    return path


@contextmanager
def growing_after_first_read(path, extra):
    """Append to path once the first chunk has been read, as a concurrent writer would."""
    real, grown = os.fdopen, []
    class Stream:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): self.stream.__enter__(); return self
        def __exit__(self, *error): return self.stream.__exit__(*error)
        def read(self, size):
            raw = self.stream.read(size)
            if not grown:
                grown.append(True)
                with open(path, 'ab') as writer: writer.write(extra)
            return raw
    with patch.object(integrity.os, 'fdopen', side_effect=lambda *a, **k: Stream(real(*a, **k))): yield


class TestHelperEquivalence:
    @pytest.fixture(autouse=True)
    def _root(self, tmp_path):
        self.root = tmp_path

    def write(self, name, raw):
        path = self.root/name; path.write_bytes(raw); return path

    def whole(self, path, limit):
        return dict(sha=outcome(lambda: hashlib.sha256(read_regular(path, limit)).hexdigest()),
                    size=outcome(lambda: len(read_regular(path, limit))))

    def chunked(self, path, limit):
        return dict(sha=outcome(lambda: regular_sha256(path, limit)), size=outcome(lambda: regular_size(path, limit)))

    @pytest.mark.parametrize('size,limit', BOUNDARIES)
    def test_digest_and_size_match_whole_reads_at_every_limit_boundary(self, size, limit):
        raw = boundary_bytes(size); path = self.write(f'f{size}', raw)
        expected = self.whole(path, limit)
        assert self.chunked(path, limit) == expected
        assert outcome(lambda: regular_equal(path, raw, limit)) == outcome(lambda: read_regular(path, limit) == raw)
        if limit is None or limit >= size:
            assert expected['sha'] == ('value', hashlib.sha256(raw).hexdigest())
        else:
            assert expected['sha'] == ('ValueError', 'input must be a bounded single-link regular file')

    def test_compare_detects_every_kind_of_difference(self):
        raw = random.Random(7).randbytes(2*CHUNK+3); path = self.write('payload', raw)
        assert regular_equal(path, raw, None)
        for other in (raw[:-1], raw+b'x', raw[:CHUNK]+bytes([raw[CHUNK]^1])+raw[CHUNK+1:], b'', bytearray(raw)):
            assert regular_equal(path, other, None) == (read_regular(path, None) == other)

    @pytest.mark.parametrize('name', [pytest.param(name, id=name) for name in UNSAFE])
    def test_unsafe_paths_raise_the_same_errors(self, name):
        target = self.write('target', b'synthetic')
        (self.root/'link').symlink_to(target)
        (self.root/'folder').mkdir(); (self.root/'folder'/'inner').write_bytes(b'inner')
        (self.root/'parent').symlink_to(self.root/'folder')
        os.link(self.write('linked', b'synthetic'), self.root/'second-link')
        os.mkfifo(self.root/'fifo')
        path = self.root/name
        expected = self.whole(path, 1024)
        assert expected['sha'][0] != 'value'
        assert self.chunked(path, 1024) == expected
        assert outcome(lambda: regular_equal(path, b'synthetic', 1024)) == expected['sha']

    @pytest.mark.parametrize('helper', GROWING)
    def test_growth_during_read_is_rejected_like_a_whole_read(self, helper):
        path = self.write('growing', b'g'*(CHUNK+5))
        with growing_after_first_read(path, b'h'*64):
            assert outcome(lambda: helper(path)) == ('ValueError', 'input byte limit exceeded')

    def test_peak_memory_guard_measures_a_whole_read(self, large):
        expected = hashlib.sha256(large.read_bytes()).hexdigest(); size = large.stat().st_size
        whole, whole_peak = peak(lambda: hashlib.sha256(read_regular(large, None)).hexdigest())
        assert whole == expected; assert whole_peak > 1.5*size  # the guard measures something

    @pytest.mark.parametrize('name', ['sha256', 'size'])
    def test_peak_memory_is_a_small_multiple_of_the_chunk_size(self, large, name):
        expected = hashlib.sha256(large.read_bytes()).hexdigest(); size = large.stat().st_size
        call, value = dict(sha256=(lambda: regular_sha256(large, None), expected),
                           size=(lambda: regular_size(large, None), size))[name]
        result, used = peak(call)
        assert result == value; assert used < 4*CHUNK


class TestSnapshotPin:
    def test_streamed_repr_matches_repr_for_every_quote_and_escape(self):
        from agent_harness_lab.evaluation.derivations import _bytes_repr
        rng = random.Random(221); alphabet = (b"'", b'"', b'\\', b'\n', b'\r', b'\t', b'\0', b'a', b'\x7f', b'\xff')
        values = [b'', b"'", b'"', b'\'"', bytes(range(256))]
        values += [b''.join(rng.choice(alphabet) if rng.random() < .7 else bytes([rng.randrange(256)])
                            for _ in range(rng.randrange(1, 48))) for _ in range(3000)]
        for raw in values:
            for size in (1, 2, 3, 7, 65536):
                assert ''.join(_bytes_repr(raw, size)) == repr(raw), (raw, size)

    def test_pins_match_the_repr_digest(self):
        from agent_harness_lab.evaluation.derivations import _snapshot_pin
        from agent_harness_lab.evaluation.working_state import Snapshot
        rng = random.Random(4)
        def snapshot(count):
            files = tuple(sorted((f'dir/n{i}-é ' + rng.choice(("'", '"', '')), rng.randrange(0o777),
                                  rng.randbytes(rng.randrange(0, 70000))) for i in range(count)))
            return Snapshot(rng.randrange(0o777), tuple((f'dir{i}', 0o755) for i in range(rng.randrange(3))), files)
        class Derived(Snapshot): pass
        values = [snapshot(count) for count in (0, 1, 1, 2, 5)]
        values += [Snapshot(0o700, (), [('a', 0o600, b'x')]), Snapshot(0o700, (), (('a', 0o600, bytearray(b'x')),)),
                   Snapshot(0o700, (), (('a', True, b'x'),)), Derived(0o700, (), (('a', 0o600, b"'"),))]
        for value in values:
            assert _snapshot_pin(value) == hashlib.sha256(repr(value).encode()).hexdigest()

    def test_analysis_pins_are_unchanged_and_no_longer_hold_an_escaped_copy(self, tmp_path):
        from agent_harness_lab.evaluation.construction_access import _input_pin
        from agent_harness_lab.evaluation.derivations import _snapshot_pin
        from agent_harness_lab.evaluation.working_state import analysis_snapshot
        root = tmp_path/'workspace'; (root/'nested').mkdir(parents=True)
        (root/'nested'/'quote.txt').write_bytes(b"it's")
        (root/'large.bin').write_bytes(random.Random(5).randbytes(8*MiB))
        deadline = time.monotonic()+60; captured = analysis_snapshot(root, deadline)
        expected = hashlib.sha256(repr(captured).encode()).hexdigest()
        assert _input_pin(root, deadline, analysis=True) == expected
        tracemalloc.start()
        try: pin = _snapshot_pin(captured); used = tracemalloc.get_traced_memory()[1]
        finally: tracemalloc.stop()
        assert pin == expected; assert used < 2*MiB


class TestSiteEquivalence:
    @pytest.fixture(autouse=True)
    def _root(self, tmp_path):
        self.root = tmp_path

    def test_closed_source_pin_matches_whole_read_and_keeps_its_bound(self):
        from agent_harness_lab.evaluation import closed_inputs
        path = self.root/'source.jsonl'; deadline = time.monotonic()+60
        raw = random.Random(9).randbytes(3*MiB+1); path.write_bytes(raw); path.chmod(0o600)
        source = dict(kind='native_source_file', selection=str(path))
        assert closed_inputs.source_pin(source, deadline) == closed_inputs.pin(dict(mode=0o600, sha256=hashlib.sha256(raw).hexdigest()))
        os.truncate(path, 32*MiB); assert isinstance(closed_inputs.source_pin(source, deadline), str)
        os.truncate(path, 32*MiB+1)
        assert outcome(lambda: closed_inputs.source_pin(source, deadline)) == ('ValueError', 'input must be a bounded single-link regular file')

    @pytest.mark.parametrize('helper', ['sandbox', 'benchmark_models'])
    @pytest.mark.parametrize('name', ['binary', 'alias', 'missing'])
    def test_followed_file_digests_match_read_bytes(self, helper, name):
        from agent_harness_lab.evaluation.sandbox import _file_sha256
        from tools.benchmark_models import file_sha256
        helper = dict(sandbox=_file_sha256, benchmark_models=file_sha256)[helper]
        raw = random.Random(3).randbytes(2*MiB+7); path = self.root/'binary'; path.write_bytes(raw)
        (self.root/'alias').symlink_to(path)
        assert outcome(lambda: helper(self.root/name)) == \
            outcome(lambda: hashlib.sha256((self.root/name).read_bytes()).hexdigest())


class TestLineageCache:
    @pytest.fixture(autouse=True)
    def _lineage(self):
        from dataclasses import replace
        from agent_harness_lab.sources.lineage import LineageStore
        from agent_harness_lab.sources.schema import digest
        # The source store fixture is a unittest.TestCase owned by test_source_store; it manages its own files.
        self.fixture = fixtures.SourceStoreTests()
        try:
            self.fixture.setUp()
            f = self.fixture; self.digest, self.replace = digest, replace
            self.records = [replace(f.record, native_id_sha256=digest(n), revision_sha256=digest('revision-'+n)) for n in 'ab']
            f.store.ingest(f.source, self.records)
            self.store = LineageStore(f.root/'lineage', project_sha256=f.source.project_sha256, scope_sha256=f.source.scope_sha256)
            yield
        finally:
            self.fixture.doCleanups()

    def claim(self):
        from agent_harness_lab.sources.execution import RecordRef
        from agent_harness_lab.sources.lineage import LineageClaim
        refs = [RecordRef(r.source_sha256, r.native_id_sha256, r.revision_sha256) for r in self.records]
        path = self.fixture.root/'claim.json'
        path.write_text(json.dumps(LineageClaim(refs[0], refs[1], 'same_observation', 'synthetic-author', tuple(refs)).to_dict()))
        return path

    def cache_states(self, path):
        f = self.fixture
        newer = self.replace(f.record, native_id_sha256=self.digest('c'), revision_sha256=self.digest('revision-c'))
        return dict(same=lambda: None, appended=lambda: f.store.ingest(f.source, [newer]),
                    broken=lambda: path.write_text('{broken'), missing=path.unlink)

    @pytest.mark.parametrize('state', CACHE_STATES)
    def test_unchanged_agrees_with_a_full_reread_for_every_cache_state(self, state):
        # States accumulate in order, as one cache would see them: each case applies the earlier states first.
        f = self.fixture; path = f.store.path; identity = self.store.cache(path)[3]
        def reread(): return self.store.cache(path)[3] == identity
        states = self.cache_states(path)
        for name in CACHE_STATES[:CACHE_STATES.index(state)+1]: states[name]()
        assert outcome(lambda: self.store.unchanged(path, identity)) == outcome(reread)

    def test_unchanged_agrees_with_a_full_reread_through_an_aliased_parent(self):
        f = self.fixture; path = f.store.path; identity = self.store.cache(path)[3]
        states = self.cache_states(path)
        for name in CACHE_STATES: states[name]()
        alias = f.root/'alias'; alias.symlink_to(path.parent)
        assert outcome(lambda: self.store.unchanged(alias/path.name, identity)) == \
            outcome(lambda: self.store.cache(alias/path.name)[3] == identity)

    @pytest.mark.parametrize('change,message', CAPTURE_CHANGES)
    def test_propose_rejects_a_cache_changed_during_capture_with_the_same_error(self, change, message):
        from agent_harness_lab.sources.lineage import LineageStore
        f = self.fixture; original = LineageStore.snapshot
        newer = self.replace(f.record, native_id_sha256=self.digest('c'), revision_sha256=self.digest('revision-c'))
        change = dict(appended=lambda: f.store.ingest(f.source, [newer]),
                      broken=lambda: f.store.path.write_text('{broken'))[change]
        def snapshot(store, raw, identity):
            result = original(store, raw, identity); change(); return result
        with patch.object(LineageStore, 'snapshot', snapshot):
            assert outcome(lambda: self.store.propose(f.store.path, self.claim())) == ('ValueError', message)
        assert self.store.cases() == []
        f.store.path.unlink(); f.store.ingest(f.source, self.records)

