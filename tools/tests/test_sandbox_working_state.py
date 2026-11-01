"""Descriptor-based working snapshots; these tests do not prove kernel isolation."""
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch
from agent_harness_lab.evaluation.working_state import WorkingState
from agent_harness_lab.evaluation import working_state
if __package__:
    from . import test_sandbox_storage as fixtures
else:
    import test_sandbox_storage as fixtures


class WorkingStateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.StorageTests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.root, self.source, self.private = self.fixture.root,self.fixture.source,self.fixture.private
        self.deadline = time.monotonic()+10
        (self.source/'script').write_text('before'); (self.source/'script').chmod(0o750)
        (self.source/'empty').mkdir(); self.state = WorkingState(self.source,self.deadline)

    def descriptor(self, path):
        fd = os.open(path,os.O_RDONLY|os.O_DIRECTORY); self.addCleanup(os.close,fd)
        return fd

    def test_edits_file_and_empty_directory_deletion_and_modes_survive_fresh_seeding(self):
        scratch = self.fixture.opened(); scratch.working_state = self.state; scratch.seed()
        self.assertEqual((self.private/'script').read_text(),'before')
        (self.private/'script').unlink(); (self.private/'empty').rmdir()
        (self.private/'nested').mkdir(); (self.private/'nested/next').write_text('after')
        (self.private/'nested/next').chmod(0o710); (self.private/'nested').chmod(0o500)
        report = scratch.extract(); self.assertEqual(report,dict(files=1,directories=1,bytes=5))
        next_work = self.root/'next'; next_work.mkdir()
        self.state.seed(self.descriptor(next_work),self.deadline)
        self.assertFalse((next_work/'script').exists()); self.assertFalse((next_work/'empty').exists())
        self.assertEqual((next_work/'nested/next').read_text(),'after')
        self.assertEqual((next_work/'nested/next').stat().st_mode&0o777,0o710)
        self.assertEqual((next_work/'nested').stat().st_mode&0o777,0o500)
        self.assertEqual((self.source/'script').read_text(),'before')
        (next_work/'nested').chmod(0o700); (self.private/'nested').chmod(0o700)

    def test_seed_rejects_merge_into_a_nonempty_workspace(self):
        with self.assertRaisesRegex(ValueError,'empty directory'):
            self.state.seed(self.descriptor(self.source),self.deadline)

    def test_initial_files_bytes_directories_and_special_modes_are_bounded(self):
        for kind in ('files','bytes','directories','special'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.root) as folder:
                root = Path(folder)
                if kind == 'files':
                    for index in range(101): (root/str(index)).touch()
                elif kind == 'directories':
                    for index in range(101): (root/str(index)).mkdir()
                else:
                    target = root/'value'; target.write_bytes(b'x'*(1024*1024+1) if kind=='bytes' else b'x')
                    if kind=='special': target.chmod(0o4750)
                with self.assertRaises(ValueError): WorkingState(root,self.deadline)

    def test_links_special_files_and_late_limit_failure_cannot_partially_replace_state(self):
        before = self.state.snapshot; descriptor = self.descriptor(self.private)
        for kind in ('symlink','hardlink','fifo','bytes'):
            with self.subTest(kind=kind):
                for path in self.private.iterdir(): path.unlink()
                good = self.private/'good'; good.write_text('uncommitted change')
                bad = self.private/'bad'
                if kind=='symlink': bad.symlink_to(self.source/'script')
                elif kind=='hardlink': os.link(good,bad)
                elif kind=='fifo': os.mkfifo(bad)
                else: bad.write_bytes(b'x'*(1024*1024+1))
                with self.assertRaises((ValueError,OSError)): self.state.capture(descriptor,self.deadline)
                self.assertIs(self.state.snapshot,before)

    def test_deadline_failure_keeps_prior_snapshot_and_exports_nothing(self):
        before = self.state.snapshot; (self.private/'next').write_text('new')
        with self.assertRaises(TimeoutError): self.state.capture(self.descriptor(self.private),0)
        self.assertIs(self.state.snapshot,before)
        destination = self.root/'artifacts'
        with self.assertRaises(TimeoutError): self.state.export(['script'],destination,0)
        self.assertFalse(destination.exists()); self.assertFalse(list(self.root.glob('.artifact-stage-*')))

    def test_final_exports_validate_every_artifact_and_publish_as_one_directory(self):
        destination = self.root/'artifacts'
        with self.assertRaises(ValueError): self.state.export(['script','missing'],destination,self.deadline)
        self.assertFalse(destination.exists())
        original = Path.write_bytes
        def fail(path,data):
            if path.name == 'script': raise OSError('synthetic disk error')
            return original(path,data)
        with patch.object(Path,'write_bytes',fail), self.assertRaises(OSError):
            self.state.export(['script'],destination,self.deadline)
        self.assertFalse(destination.exists()); self.assertFalse(list(self.root.glob('.artifact-stage-*')))
        self.state.export(['script'],destination,self.deadline)
        self.assertEqual((destination/'script').read_text(),'before')
        self.assertEqual((destination/'script').stat().st_mode&0o777,0o600)
        with self.assertRaises(ValueError): self.state.export(['script'],destination,self.deadline)

    def test_complete_publication_preserves_dynamic_names_modes_and_empty_directories(self):
        (self.source/'new').mkdir(); (self.source/'new/authored.py').write_text('synthetic')
        (self.source/'new/authored.py').chmod(0o710); (self.source/'script').unlink()
        state = WorkingState(self.source,self.deadline)
        destination = state.publish(self.root/'authored',self.deadline)
        self.assertTrue((destination/'empty').is_dir()); self.assertFalse((destination/'script').exists())
        self.assertEqual((destination/'new/authored.py').read_text(),'synthetic')
        self.assertEqual((destination/'new/authored.py').stat().st_mode&0o777,0o710)

    def test_complete_publication_failure_never_admits_a_partial_tree(self):
        (self.source/'zz-last').write_text('last'); state = WorkingState(self.source,self.deadline)
        original = working_state.write_file
        def fail(root,name,raw,mode):
            if name=='zz-last': raise OSError('synthetic late filesystem failure')
            return original(root,name,raw,mode)
        destination = self.root/'authored'
        with patch.object(working_state,'write_file',side_effect=fail),self.assertRaises(OSError):
            state.publish(destination,self.deadline)
        self.assertFalse(destination.exists()); self.assertFalse(list(self.root.glob('.artifact-stage-*')))
