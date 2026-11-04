"""Observed project inputs stay bounded independently of unread checkout size."""
import os
from pathlib import Path
import tempfile
import time
import unittest

from agent_harness_lab.evaluation import closed_inputs as closed
from agent_harness_lab.evaluation.working_state import WorkingState


class ClosedProjectInputTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.project=self.root/'project';self.project.mkdir()
        (self.project/'AGENTS.md').write_text('Synthetic instructions.')
        (self.project/'ahl.json').write_text('{"synthetic":"complete configuration"}')
        self.deadline=time.monotonic()+10;self.selection=closed.project_selection(self.project)
        self.declaration=dict(project_inputs=self.selection,project_sha256=closed.snapshot_pin(
            closed.project_snapshot(self.project,self.selection,self.deadline)))

    def test_unread_large_checkout_does_not_become_an_input_or_exceed_its_bounds(self):
        git=self.project/'.git';git.mkdir();(git/'large-object').write_bytes(b'x'*(1024*1024+1))
        for index in range(101):(self.project/('unrelated-'+str(index))).write_text('Unobserved.')
        # This reproduces the former whole-checkout failure on the same tree.
        with self.assertRaises(ValueError):WorkingState(self.project,self.deadline)
        original=closed.check_project(self.project,self.declaration,self.deadline)
        (git/'large-object').write_text('Changed but not a controller input.')
        (self.project/'unrelated-0').write_text('Still unobserved.')
        self.assertEqual(closed.check_project(self.project,self.declaration,self.deadline),original)
        self.assertEqual({name for name,_,_ in original.files},{'AGENTS.md','ahl.json'})

    def test_every_bound_file_bytes_and_modes_are_revalidated(self):
        for name in ('AGENTS.md','ahl.json'):
            with self.subTest(name=name):
                path=self.project/name;before=path.read_bytes();mode=path.stat().st_mode&0o777
                path.write_bytes(before+b' ')
                with self.assertRaises(ValueError):closed.check_project(self.project,self.declaration,self.deadline)
                path.write_bytes(before);path.chmod(mode^0o010)
                with self.assertRaises(ValueError):closed.check_project(self.project,self.declaration,self.deadline)
                path.chmod(mode)

    def test_group_or_world_writable_signed_inputs_are_refused(self):
        for name in ('AGENTS.md','ahl.json'):
            for extra in (0o020,0o002):
                with self.subTest(name=name,mode=oct(extra)):
                    path=self.project/name;mode=path.stat().st_mode&0o777;path.chmod(mode|extra)
                    with self.assertRaisesRegex(ValueError,'not group or world writable'):
                        closed.project_snapshot(self.project,self.selection,self.deadline)
                    path.chmod(mode)
        events=self.root/'events.jsonl';events.write_text('');events.chmod(0o620)
        source=dict(kind='events_file',selection=str(events))
        with self.assertRaisesRegex(ValueError,'not group or world writable'):closed.source_pin(source,self.deadline)
        events.chmod(0o600);self.assertIsInstance(closed.source_pin(source,self.deadline),str)

    def test_root_mode_membership_and_exact_project_identity_are_bound(self):
        mode=self.project.stat().st_mode&0o777;self.project.chmod(mode^0o010)
        with self.assertRaises(ValueError):closed.check_project(self.project,self.declaration,self.deadline)
        self.project.chmod(mode);(self.project/'AGENTS.md').unlink()
        with self.assertRaises(ValueError):closed.check_project(self.project,self.declaration,self.deadline)
        clone=self.root/'clone';clone.mkdir()
        with self.assertRaises(ValueError):closed.project_snapshot(clone,self.selection,self.deadline)

    def test_missing_configuration_and_unsafe_bound_members_are_refused(self):
        (self.project/'ahl.json').unlink()
        with self.assertRaises(ValueError):closed.project_snapshot(self.project,self.selection,self.deadline)
        (self.project/'ahl.json').symlink_to(self.project/'AGENTS.md')
        with self.assertRaises(ValueError):closed.project_snapshot(self.project,self.selection,self.deadline)

    def test_absent_agents_is_explicit_membership_and_does_not_allow_other_scopes(self):
        (self.project/'AGENTS.md').unlink()
        before=closed.project_snapshot(self.project,self.selection,self.deadline)
        self.assertEqual([name for name,_,_ in before.files],['ahl.json'])
        selection=dict(self.selection,paths=['CLAUDE.md','ahl.json'])
        with self.assertRaises(ValueError):closed.project_snapshot(self.project,selection,self.deadline)
