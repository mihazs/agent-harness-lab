"""A symlinked instruction file is refused as an unsupported layout whatever its target looks like (#807)."""
import pytest

from agent_harness_lab import typed_exit, workflow


def project_with(tmp_path, shape):
    project = tmp_path / 'project'
    project.mkdir()
    link = project / 'AGENTS.md'
    if shape == 'live': (project / 'real.md').write_text('synthetic\n'); link.symlink_to('real.md')
    elif shape == 'dangling': link.symlink_to('missing.md')
    else: link.symlink_to('AGENTS.md')
    return project


@pytest.mark.parametrize('shape', ['live', 'dangling', 'loop'])
def test_a_symlinked_instruction_file_is_listed_so_the_snapshot_can_refuse_it(tmp_path, shape):
    project = project_with(tmp_path, shape)
    assert [path.name for path in workflow.harness_files(project)] == ['AGENTS.md']


@pytest.mark.parametrize('shape', ['live', 'dangling', 'loop'])
def test_a_symlinked_instruction_file_is_refused_with_the_typed_layout_reason(tmp_path, shape):
    project = project_with(tmp_path, shape)
    with pytest.raises(typed_exit.TypedExit) as refused:
        workflow.snapshot(project, tmp_path / 'baseline')
    assert refused.value.reason == 'unsupported_instruction_layout'


def test_a_project_without_instruction_files_lists_none(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    assert workflow.harness_files(project) == []
