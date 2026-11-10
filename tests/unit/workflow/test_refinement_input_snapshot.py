"""Filesystem ancestry checks only; these fixtures issue no live authority."""
from copy import deepcopy
import json
import shutil
import time
from unittest.mock import patch

import pytest

from agent_harness_lab import workflow
from agent_harness_lab.evaluation import closed_inputs as closed


class RefinementInputs:
    """An original proposer input, its refinement copy and the owned feedback context that binds them."""

    def __init__(self,root):
        self.root=root;self.original=self.root/'original';self.original.mkdir(mode=0o750)
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


@pytest.fixture
def inputs(tmp_path):
    return RefinementInputs(tmp_path)


def test_exact_owned_feedback_preserves_the_full_projection(inputs):
    assert inputs.check()['parent_construction_id'] == 'synthetic-parent'


def test_current_original_and_copy_rewrite_cannot_replace_the_stored_projection(inputs):
    for root in (inputs.original,inputs.context):(root/'summary.json').write_text('{"changed":true}')
    with pytest.raises(ValueError):inputs.check()


def test_original_and_refinement_root_modes_cannot_rebase_projection(inputs):
    for root in (inputs.original,inputs.context):root.chmod(0o700)
    with pytest.raises(ValueError):inputs.check()


def test_original_and_refinement_directory_modes_cannot_rebase_projection(inputs):
    for root in (inputs.original,inputs.context):(root/'methodology').chmod(0o700)
    with pytest.raises(ValueError):inputs.check()


def test_refinement_directory_mode_is_not_an_allowed_feedback_transform(inputs):
    (inputs.context/'methodology').chmod(0o700)
    with pytest.raises(ValueError):inputs.check()


def test_refinement_root_mode_is_not_an_allowed_feedback_transform(inputs):
    inputs.context.chmod(0o700)
    with pytest.raises(ValueError):inputs.check()


def test_unbound_extra_file_or_directory_is_not_feedback(inputs):
    (inputs.context/'extra').mkdir()
    with pytest.raises(ValueError):inputs.check()


def test_feedback_mode_and_canonical_bytes_must_match_the_owned_writer(inputs):
    path=inputs.context/'feedback.json';path.chmod(0o640)
    with pytest.raises(ValueError):inputs.check()
    path.chmod(0o600);path.write_text(json.dumps(dict(trials=inputs.rows)))
    with pytest.raises(ValueError):inputs.check()


def test_unrecorded_feedback_context_cannot_supply_a_parent(inputs):
    inputs.value.pop('feedback_context')
    with pytest.raises(ValueError):inputs.check()


def test_actual_controller_writer_records_exact_canonical_feedback_file(inputs):
    fresh=inputs.root/'fresh-refinement';shutil.copytree(inputs.original,fresh)
    with patch.object(closed,'owned',return_value=inputs.value):
        closed.write_feedback(object(),fresh/'feedback.json',inputs.rows)
    inputs.context=fresh
    assert inputs.check()['feedback'] == inputs.feedback
    assert (fresh/'feedback.json').stat().st_mode&0o777 == 0o600


def test_writer_rejects_rows_outside_native_feedback_before_writing(inputs):
    fresh=inputs.root/'fresh-refinement';shutil.copytree(inputs.original,fresh)
    with patch.object(closed,'owned',return_value=inputs.value),patch.object(workflow,'write_json') as write:
        with pytest.raises(ValueError):closed.write_feedback(object(),fresh/'feedback.json',[])
        write.assert_not_called()
