"""Filesystem ancestry checks only; these fixtures issue no live authority."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_harness_lab import workflow
from agent_harness_lab.evaluation import closed_inputs as closed


class RefinementInputSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.original=self.root/'original';self.original.mkdir(mode=0o750)
        (self.original/'methodology').mkdir(mode=0o750)
        (self.original/'methodology/source.md').write_text('Synthetic original input.')
        (self.original/'summary.json').write_text('{}')
        self.deadline=time.monotonic()+10
        original=dict(path=str(self.original),snapshot_sha256=closed.tree_pin(self.original,self.deadline))
        self.context=self.root/'refinement';shutil.copytree(self.original,self.context)
        self.rows=[dict(task='synthetic-task',version='candidate',split='development',status='passed')]
        self.feedback=dict(rows=deepcopy(self.rows),native_pairs=[dict(executor='synthetic',grader='synthetic')],evidence_sha256='a'*64)
        workflow.write_json(self.context/'feedback.json',dict(trials=self.rows))
        raw=(json.dumps(dict(trials=self.rows),indent=2,allow_nan=False)+'\n').encode()
        self.access=object();self.value=dict(access=self.access,views={'proposer':original},projection={'synthetic':'projection'},
            deadline=self.deadline,stages=[dict(construction_id='synthetic-parent')],feedback=self.feedback,
            feedback_context=dict(path=str(self.context),file=('feedback.json',0o600,raw),feedback_sha256=closed.pin(self.feedback)))

    def check(self):
        with patch.object(closed,'owned',return_value=self.value):
            return closed.stage_input(object(),self.access,'proposer',self.context,parent='synthetic-parent',feedback=self.rows)

    def test_exact_owned_feedback_preserves_the_full_projection(self):
        self.assertEqual(self.check()['parent_construction_id'],'synthetic-parent')

    def test_current_original_and_copy_rewrite_cannot_replace_the_stored_projection(self):
        for root in (self.original,self.context):(root/'summary.json').write_text('{"changed":true}')
        with self.assertRaises(ValueError):self.check()

    def test_original_and_refinement_root_modes_cannot_rebase_projection(self):
        for root in (self.original,self.context):root.chmod(0o700)
        with self.assertRaises(ValueError):self.check()

    def test_original_and_refinement_directory_modes_cannot_rebase_projection(self):
        for root in (self.original,self.context):(root/'methodology').chmod(0o700)
        with self.assertRaises(ValueError):self.check()

    def test_refinement_directory_mode_is_not_an_allowed_feedback_transform(self):
        (self.context/'methodology').chmod(0o700)
        with self.assertRaises(ValueError):self.check()

    def test_refinement_root_mode_is_not_an_allowed_feedback_transform(self):
        self.context.chmod(0o700)
        with self.assertRaises(ValueError):self.check()

    def test_unbound_extra_file_or_directory_is_not_feedback(self):
        (self.context/'extra').mkdir()
        with self.assertRaises(ValueError):self.check()

    def test_feedback_mode_and_canonical_bytes_must_match_the_owned_writer(self):
        path=self.context/'feedback.json';path.chmod(0o640)
        with self.assertRaises(ValueError):self.check()
        path.chmod(0o600);path.write_text(json.dumps(dict(trials=self.rows)))
        with self.assertRaises(ValueError):self.check()

    def test_unrecorded_feedback_context_cannot_supply_a_parent(self):
        self.value.pop('feedback_context')
        with self.assertRaises(ValueError):self.check()

    def test_actual_controller_writer_records_exact_canonical_feedback_file(self):
        fresh=self.root/'fresh-refinement';shutil.copytree(self.original,fresh)
        with patch.object(closed,'owned',return_value=self.value):
            closed.write_feedback(object(),fresh/'feedback.json',self.rows)
        self.context=fresh
        self.assertEqual(self.check()['feedback'],self.feedback)
        self.assertEqual((fresh/'feedback.json').stat().st_mode&0o777,0o600)

    def test_writer_rejects_rows_outside_native_feedback_before_writing(self):
        fresh=self.root/'fresh-refinement';shutil.copytree(self.original,fresh)
        with patch.object(closed,'owned',return_value=self.value),patch.object(workflow,'write_json') as write:
            with self.assertRaises(ValueError):closed.write_feedback(object(),fresh/'feedback.json',[])
            write.assert_not_called()
