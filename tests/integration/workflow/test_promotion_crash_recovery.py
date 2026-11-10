"""Abrupt process death at real promotion writes, using synthetic files only."""
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from agent_harness_lab.promotion.journal import Journal


CHILD = r'''
import os
from pathlib import Path
import sys
from agent_harness_lab.promotion import journal as module

project, store, point, guarded = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4] == 'guarded'
journal = module.Journal(project, store)
names = ('AGENTS.md', 'CLAUDE.md')
before = {name: module.file_record(project / name) for name in names}
after = {name: module.snapshot_record(('new ' + name).encode(), 0o600) for name in names}
original_atomic = module.atomic_json
def atomic(path, value, *args, **kwargs):
    result = original_atomic(path, value, *args, **kwargs)
    if point == 'prepared' and value.get('status') == 'prepared': os._exit(99)
    if point == 'committed' and value.get('status') == 'committed': os._exit(99)
    return result
module.atomic_json = atomic
original_replace = module.os.replace
def replace(source, target, *args, **kwargs):
    target = Path(target)
    is_target = target.parent == project and target.name in names
    if is_target and (point == 'temporary' or point == 'multi' and target.name == 'CLAUDE.md'):
        os._exit(99)
    result = original_replace(source, target, *args, **kwargs)
    if is_target and point == 'renamed': os._exit(99)
    return result
module.os.replace = replace
record = journal.prepare(before, after)
if guarded:
    from agent_harness_lab.workflow import project_lock
    with project_lock(project): journal._execute(record['deployment'], pre_write=lambda: None)
else:
    journal.execute(record['deployment'])
raise AssertionError('injection point was not reached')
'''


def launch_crash(tmp_path, point, guarded=False):
    project = tmp_path / 'project'
    project.mkdir()
    for name, mode in (('AGENTS.md', 0o640), ('CLAUDE.md', 0o644)):
        path = project / name
        path.write_bytes(('original ' + name).encode())
        path.chmod(mode)
    expected = {path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(path.stat().st_mode))
                for path in project.iterdir()}
    store = tmp_path / 'journal'
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[3]),
                       XDG_STATE_HOME=str(tmp_path / 'state'))
    result = subprocess.run([sys.executable, '-c', CHILD, str(project), str(store), point,
                             'guarded' if guarded else 'unguarded'],
                            capture_output=True, text=True, env=environment, timeout=20)
    assert result.returncode == 99, result.stderr
    records = list(store.glob('*.json'))
    assert len(records) == 1
    return project, Journal(project, store), records[0].stem, expected


@pytest.mark.parametrize('point', ['prepared', 'temporary', 'renamed', 'multi'])
@pytest.mark.parametrize('guarded', [False, True], ids=['unguarded', 'guarded'])
def test_recovery_after_process_death_restores_exact_bytes_and_mode(tmp_path, point, guarded):
    project, journal, deployment, expected = launch_crash(tmp_path, point, guarded)
    if point in ('renamed', 'multi'):
        assert (project / 'AGENTS.md').read_bytes() == b'new AGENTS.md'
        assert (project / 'CLAUDE.md').read_bytes() == b'original CLAUDE.md'
    if point == 'temporary':
        assert list(project.glob('AGENTS.md.ahl-*'))
    if point == 'multi':
        assert list(project.glob('CLAUDE.md.ahl-*'))
    assert journal.recover(deployment)['status'] == 'recovered'
    for name, (digest, mode) in expected.items():
        path = project / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        assert stat.S_IMODE(path.stat().st_mode) == mode


def test_foreign_edit_after_partial_apply_blocks_all_restoration(tmp_path):
    project, journal, deployment, _ = launch_crash(tmp_path, 'multi')
    edited = project / 'CLAUDE.md'
    edited.write_bytes(b'synthetic external edit')
    before = {name: ((project / name).read_bytes(), stat.S_IMODE((project / name).stat().st_mode))
              for name in ('AGENTS.md', 'CLAUDE.md')}
    with pytest.raises(ValueError, match='intervening edit/mode/path conflict'):
        journal.recover(deployment)
    assert {name: ((project / name).read_bytes(), stat.S_IMODE((project / name).stat().st_mode))
            for name in before} == before
    assert journal.load(deployment)['status'] == 'applying'


def test_crash_after_committed_journal_refuses_unsafe_recovery_with_named_state(tmp_path):
    project, journal, deployment, _ = launch_crash(tmp_path, 'committed')
    assert journal.load(deployment)['status'] == 'committed'
    assert journal.active is None
    before = {name: (project / name).read_bytes() for name in ('AGENTS.md', 'CLAUDE.md')}
    with pytest.raises(ValueError, match='only an unfinished deployment can be recovered'):
        journal.recover(deployment)
    assert {name: (project / name).read_bytes() for name in before} == before
