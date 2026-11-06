"""Observed project inputs stay bounded independently of unread checkout size."""
from types import SimpleNamespace
import time

import pytest

from agent_harness_lab.evaluation import closed_inputs as closed
from agent_harness_lab.evaluation.working_state import WorkingState

BOUND=('AGENTS.md','ahl.json')


@pytest.fixture
def case(tmp_path):
    root=tmp_path;project=root/'project';project.mkdir()
    (project/'AGENTS.md').write_text('Synthetic instructions.')
    (project/'ahl.json').write_text('{"synthetic":"complete configuration"}')
    deadline=time.monotonic()+10;selection=closed.project_selection(project)
    declaration=dict(project_inputs=selection,project_sha256=closed.snapshot_pin(
        closed.project_snapshot(project,selection,deadline)))
    return SimpleNamespace(root=root,project=project,deadline=deadline,selection=selection,declaration=declaration)


def test_unread_large_checkout_does_not_become_an_input_or_exceed_its_bounds(case):
    git=case.project/'.git';git.mkdir();(git/'large-object').write_bytes(b'x'*(1024*1024+1))
    for index in range(101):(case.project/('unrelated-'+str(index))).write_text('Unobserved.')
    # This reproduces the former whole-checkout failure on the same tree.
    with pytest.raises(ValueError):WorkingState(case.project,case.deadline)
    original=closed.check_project(case.project,case.declaration,case.deadline)
    (git/'large-object').write_text('Changed but not a controller input.')
    (case.project/'unrelated-0').write_text('Still unobserved.')
    assert closed.check_project(case.project,case.declaration,case.deadline)==original
    assert {name for name,_,_ in original.files}=={'AGENTS.md','ahl.json'}


@pytest.mark.parametrize('name',[pytest.param(name,id=name) for name in BOUND])
def test_every_bound_file_bytes_and_modes_are_revalidated(case,name):
    path=case.project/name;before=path.read_bytes();mode=path.stat().st_mode&0o777
    path.write_bytes(before+b' ')
    with pytest.raises(ValueError):closed.check_project(case.project,case.declaration,case.deadline)
    path.write_bytes(before);path.chmod(mode^0o010)
    with pytest.raises(ValueError):closed.check_project(case.project,case.declaration,case.deadline)
    path.chmod(mode)


@pytest.mark.parametrize('name,extra',[pytest.param(name,extra,id=f'{name}-{oct(extra)}')
    for name in BOUND for extra in (0o020,0o002)])
def test_group_or_world_writable_signed_inputs_are_refused(case,name,extra):
    path=case.project/name;mode=path.stat().st_mode&0o777;path.chmod(mode|extra)
    with pytest.raises(ValueError,match='not group or world writable'):
        closed.project_snapshot(case.project,case.selection,case.deadline)
    path.chmod(mode)


def test_group_writable_events_source_is_refused(case):
    events=case.root/'events.jsonl';events.write_text('');events.chmod(0o620)
    source=dict(kind='events_file',selection=str(events))
    with pytest.raises(ValueError,match='not group or world writable'):closed.source_pin(source,case.deadline)
    events.chmod(0o600);assert isinstance(closed.source_pin(source,case.deadline),str)


def test_root_mode_membership_and_exact_project_identity_are_bound(case):
    mode=case.project.stat().st_mode&0o777;case.project.chmod(mode^0o010)
    with pytest.raises(ValueError):closed.check_project(case.project,case.declaration,case.deadline)
    case.project.chmod(mode);(case.project/'AGENTS.md').unlink()
    with pytest.raises(ValueError):closed.check_project(case.project,case.declaration,case.deadline)
    clone=case.root/'clone';clone.mkdir()
    with pytest.raises(ValueError):closed.project_snapshot(clone,case.selection,case.deadline)


def test_missing_configuration_and_unsafe_bound_members_are_refused(case):
    (case.project/'ahl.json').unlink()
    with pytest.raises(ValueError):closed.project_snapshot(case.project,case.selection,case.deadline)
    (case.project/'ahl.json').symlink_to(case.project/'AGENTS.md')
    with pytest.raises(ValueError):closed.project_snapshot(case.project,case.selection,case.deadline)


def test_absent_agents_is_explicit_membership_and_does_not_allow_other_scopes(case):
    (case.project/'AGENTS.md').unlink()
    before=closed.project_snapshot(case.project,case.selection,case.deadline)
    assert [name for name,_,_ in before.files]==['ahl.json']
    selection=dict(case.selection,paths=['CLAUDE.md','ahl.json'])
    with pytest.raises(ValueError):closed.project_snapshot(case.project,selection,case.deadline)
