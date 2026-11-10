"""A repository opt-out prevents target mutations without blocking local analysis."""
import contextlib
import io
import json

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.promotion.journal import Journal, file_record, snapshot_record
from tests.integration.workflow.test_one_call import OneCallTests
from tests.support.unittest_cleanups import run_cleanups


@pytest.fixture
def project():
    one = OneCallTests(); one.setUp()
    yield one
    run_cleanups(one)


def call(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def test_frozen_project_refuses_improvement_and_initialization(project):
    marker = project.project / '.ahl-read-only'
    marker.touch()
    before = project.skill.read_bytes()
    for argv in (
        ['improve', str(project.project), '--repair-contracts'],
        ['improve', str(project.project)],
        ['watch', str(project.project), '--once'],
        ['skills', 'invoke', str(project.project), '--repair-contracts'],
        ['init', str(project.project), '--non-interactive', '--write'],
    ):
        code, _, err = call(argv)
        assert code != 0
        assert '.ahl-read-only' in err
    assert project.skill.read_bytes() == before
    assert not (project.project / 'ahl.json').exists()


def test_frozen_project_allows_analysis_but_not_output_in_project(project):
    (project.project / '.ahl-read-only').touch()
    code, out, _ = call(['analyze', str(project.project), '--output', str(project.root / 'analysis')])
    assert code == 0
    assert json.loads(out)['status'] == 'analyzed'
    code, _, err = call(['analyze', str(project.project), '--output', str(project.project / 'output')])
    assert code != 0 and '.ahl-read-only' in err
    assert not (project.project / 'output').exists()
    from agent_harness_lab import audit
    report = audit.run(project.project, 'claude-code')
    assert report.status == 'completed'


def test_frozen_project_refuses_rollback_and_recovery(project):
    code, result = project.invoke('--repair-contracts')
    assert code == 0
    deployment = result['deployment']
    applied = project.skill.read_bytes()
    (project.project / '.ahl-read-only').touch()
    for argv in (
        ['rollback', deployment, '--project', str(project.project)],
        ['promotion', str(project.project), '--deployment', deployment, '--action', 'rollback'],
        ['promotion', str(project.project), '--deployment', deployment, '--action', 'recover'],
    ):
        code, _, err = call(argv)
        assert code != 0 and '.ahl-read-only' in err
    assert project.skill.read_bytes() == applied
    (project.project / '.ahl-read-only').unlink()
    assert call(['rollback', deployment, '--project', str(project.project)])[0] == 0
    assert project.skill.read_bytes() == b'\xef\xbb\xbf' + project.valid


def test_freeze_after_preparation_prevents_journal_execution(project):
    journal = Journal(project.project, project.root / 'journal')
    name = '.agents/skills/example/SKILL.md'
    transaction = journal.prepare({name: file_record(project.skill)}, {name: snapshot_record(project.valid, 0o640)})
    (project.project / '.ahl-read-only').symlink_to('missing-marker')
    before = project.skill.read_bytes()
    with pytest.raises(ValueError, match=r'\.ahl-read-only'):
        journal.execute(transaction['deployment'])
    assert project.skill.read_bytes() == before


def test_ancestor_marker_covers_nested_projects_and_output(project):
    (project.root / '.ahl-read-only').touch()
    before = project.skill.read_bytes()
    code, _, err = call(['improve', str(project.project), '--repair-contracts'])
    assert code != 0 and '.ahl-read-only' in err
    code, _, err = call(['analyze', str(project.project), '--output', str(project.root / 'output')])
    assert code != 0 and '.ahl-read-only' in err
    assert not (project.root / 'output').exists()
    assert project.skill.read_bytes() == before


def test_generic_output_and_store_destinations_refuse_frozen_paths(project):
    (project.project / '.ahl-read-only').touch()
    alias = project.root / 'alias'
    alias.symlink_to(project.project, target_is_directory=True)
    for argv in (
        ['benchmark-report', '--input', str(project.root / 'unused.json'), '--output', str(alias / 'report')],
        ['benchmark-report', '--input', str(project.root / 'unused.json'), '--out', str(alias / 'report')],
        ['experiment-history', '--store=' + str(alias / 'store'), 'status'],
        ['bank', '--starter', '--export', str(alias / 'export')],
        ['sources', 'status', '--cache', str(alias / 'source.json')],
    ):
        code, _, err = call(argv)
        assert code != 0 and '.ahl-read-only' in err
    assert not (project.project / 'report').exists()
    assert not (project.project / 'store').exists()
    assert not (project.project / 'export').exists()


def test_shared_writers_refuse_frozen_destinations(project):
    from agent_harness_lab.monitoring.state import atomic_json
    from agent_harness_lab.private_journal import PrivateJournal, write_bytes
    (project.project / '.ahl-read-only').touch()
    destination = project.project / 'new' / 'data.json'
    for write in (lambda: atomic_json(destination, {}), lambda: write_bytes(destination, b'{}')):
        with pytest.raises(ValueError, match=r'\.ahl-read-only'):
            write()
    journal = PrivateJournal(project.project / 'store', 'synthetic', lambda *args: False)
    with pytest.raises(ValueError, match=r'\.ahl-read-only'):
        with journal.locked(create=True):
            pass
    assert not (project.project / 'new').exists()
    assert not (project.project / 'store').exists()


def test_source_cache_writer_refuses_frozen_destination(project):
    from agent_harness_lab.sources.schema import SourceSpec, digest
    from agent_harness_lab.sources.store import SourceStore
    source = SourceSpec('local_file', 'synthetic', 'v1', digest('root'), digest('project'),
                        digest('scope'), identity_provenance='source_declared_id', enabled=True)
    store = SourceStore(project.project / 'cache' / 'history.json',
                        project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
    (project.project / '.ahl-read-only').touch()
    with pytest.raises(ValueError, match=r'\.ahl-read-only'):
        store.ingest(source, [])
    assert not (project.project / 'cache').exists()


@pytest.mark.parametrize('writer', ['monitoring', 'private'])
def test_freeze_created_before_atomic_publish_stops_replace(project, writer):
    from agent_harness_lab.monitoring import state
    from agent_harness_lab import private_journal
    destination = project.project / 'receipt.json'
    write = state.atomic_json if writer == 'monitoring' else private_journal.atomic_json
    with pytest.raises(ValueError, match=r'\.ahl-read-only'):
        write(destination, {}, before_replace=lambda: (project.project / '.ahl-read-only').touch())
    assert not destination.exists()


def test_skill_install_refuses_frozen_destination_and_backup(project):
    from tools.link_skills import install
    source = project.root / 'skill-sources'
    for name in ('harness-evaluation', 'harness-self-improvement'):
        (source / name).mkdir(parents=True)
        (source / name / 'SKILL.md').write_text('synthetic')
    (project.project / '.ahl-read-only').touch()
    inside = project.project / '.agents' / 'skills' / 'new-destination'
    outside = project.root / 'outside'
    code, _, error = call(['skills', '--destination', str(inside), '--backup-root', str(outside)])
    assert code != 0 and '.ahl-read-only' in error
    assert not inside.exists() and not outside.exists()
    for destination, backup in ((inside, outside), (outside, inside)):
        with pytest.raises(ValueError, match=r'\.ahl-read-only'):
            install(source, destination, backup)
        assert not inside.exists() and not outside.exists()


def test_frozen_recovery_preserves_unfinished_journal_until_unfrozen(project):
    journal = Journal(project.project, project.root / 'journal')
    name = '.agents/skills/example/SKILL.md'
    before = project.skill.read_bytes()
    record = journal.prepare({name: file_record(project.skill)}, {name: snapshot_record(project.valid, 0o640)})
    project.skill.write_bytes(project.valid)
    marker = project.project / '.ahl-read-only'
    marker.touch()
    persisted = (journal.folder / (record['deployment'] + '.json')).read_bytes()
    with pytest.raises(ValueError, match=r'\.ahl-read-only'):
        journal.recover(record['deployment'])
    assert (journal.folder / (record['deployment'] + '.json')).read_bytes() == persisted
    assert project.skill.read_bytes() == project.valid
    marker.unlink()
    assert journal.recover(record['deployment'])['status'] == 'recovered'
    assert project.skill.read_bytes() == before
    assert project.skill.stat().st_mode & 0o777 == 0o640


def test_mid_transaction_freeze_keeps_recovery_available(project, monkeypatch):
    from agent_harness_lab.read_only import require_writable
    journal = Journal(project.project, project.root / 'journal')
    name = '.agents/skills/example/SKILL.md'
    before = project.skill.read_bytes()
    record = journal.prepare({name: file_record(project.skill)}, {name: snapshot_record(project.valid, 0o640)})
    original = journal._write_snapshot
    marker = project.project / '.ahl-read-only'
    def freeze_after_write(*args, **kwargs):
        original(*args, **kwargs)
        marker.touch()
    monkeypatch.setattr(journal, '_write_snapshot', freeze_after_write)
    with pytest.raises(ValueError, match=r'\.ahl-read-only'):
        journal._execute(record['deployment'], pre_write=lambda: require_writable(project.project))
    assert journal.load(record['deployment'])['status'] == 'applying'
    assert project.skill.read_bytes() == project.valid
    marker.unlink()
    monkeypatch.setattr(journal, '_write_snapshot', original)
    assert journal.recover(record['deployment'])['status'] == 'recovered'
    assert project.skill.read_bytes() == before
