import importlib.util
import io
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load('release_installer', ROOT / 'install.py')
builder = load('release_builder', ROOT / 'scripts/build_release.py')
syncer = load('version_syncer', ROOT / 'scripts/sync_version.py')
with patch.dict(sys.modules, {'build_release': builder}):
    publisher = load('release_publisher', ROOT / 'scripts/publish_release.py')


class Release(SimpleNamespace):
    """A synthetic Git source tree under tmp_path and the install, launcher and skill targets beside it."""

    def release(self, version='0.1.0'):
        (self.source / 'package.json').write_text(json.dumps({'version': version}))
        return builder.build(self.source, self.root / version)


@pytest.fixture
def rel(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    for name, content in [('ahl', '#!/usr/bin/env python3\nprint("synthetic CLI")\n'),
                          ('LICENSE', 'synthetic'), ('README.md', 'synthetic'),
                          ('install.py', (ROOT/'install.py').read_text()),
                          ('package.json', '{"version":"0.1.0"}'),
                          ('pyproject.toml', '[project]\nversion = "0.1.0"\n')]:
        (source / name).write_text(content)
    (source / 'agent_harness_lab').mkdir()
    (source / 'agent_harness_lab/cli.py').write_text('# synthetic')
    (source / 'tools').mkdir()
    (source / 'tools/tool.py').write_text('# synthetic tool')
    for name in installer.SKILLS:
        folder = source / 'skills' / name
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text('synthetic skill')
    subprocess.run(['git', 'init', '--quiet'], cwd=source, check=True)
    subprocess.run(['git', 'add', '.'], cwd=source, check=True)
    return Release(root=tmp_path, source=source, install_root=tmp_path / 'data', bin=tmp_path / 'bin',
                   dirs=[tmp_path / 'codex/skills', tmp_path / 'claude/skills'])


def test_real_archive_install_upgrade_and_repeat(rel):
    archive, checksums, _ = rel.release()
    destination, backup = installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs)
    assert backup is None
    assert subprocess.check_output([str(rel.bin/'ahl')], text=True).strip() == 'synthetic CLI'
    for directory in rel.dirs:
        for name in installer.SKILLS:
            assert (directory/name).resolve() == destination/'skills'/name
    assert installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs)[1] is None
    archive, checksums, _ = rel.release('0.2.0')
    upgraded, _ = installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs)
    assert destination != upgraded
    assert destination.exists()
    assert (rel.bin/'ahl').resolve() == upgraded/'ahl'


def test_existing_skill_directory_is_backed_up(rel):
    original = rel.dirs[0] / installer.SKILLS[0]
    original.mkdir(parents=True)
    (original/'SKILL.md').write_text('original')
    archive, checksums, _ = rel.release()
    _, backup = installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs)
    assert (backup/'0/SKILL.md').read_text() == 'original'


def test_update_uses_bundled_installer_and_preserves_selected_paths(rel):
    from agent_harness_lab.updater import main
    archive, checksums, _ = rel.release()
    current, _ = installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs[:1])
    archive, checksums, _ = rel.release('0.2.0')
    def response(request, timeout):
        url = request.full_url
        if url.endswith('/releases/latest'):
            return io.BytesIO(b'{"tag_name":"v0.2.0"}')
        if url.endswith('/' + archive.name): return io.BytesIO(archive.read_bytes())
        if url.endswith('/SHA256SUMS'): return io.BytesIO(checksums.read_bytes())
        raise AssertionError(url)
    with patch('agent_harness_lab.cli.assets', return_value=current), patch('urllib.request.urlopen', side_effect=response):
        assert main([]) == 0
    assert (rel.bin/'ahl').resolve() == rel.install_root/'releases/0.2.0/ahl'
    assert not rel.dirs[1].exists()
    assert json.loads((rel.install_root/'installation.json').read_text())['version'] == '0.2.0'


def test_receipt_failure_rolls_back_upgrade(rel):
    archive, checksums, _ = rel.release()
    current, _ = installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs)
    receipt = rel.install_root/'installation.json'
    old = receipt.read_bytes()
    archive, checksums, _ = rel.release('0.2.0')
    with patch.object(Path, 'replace', side_effect=OSError('synthetic receipt failure')):
        with pytest.raises(OSError):
            installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs)
    assert receipt.read_bytes() == old
    assert (rel.bin/'ahl').resolve() == current/'ahl'
    assert not (rel.install_root/'releases/0.2.0').exists()


def test_concurrent_update_cannot_publish_a_receipt_for_another_launcher(rel):
    archive, checksums, _ = rel.release()
    installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs)
    first_archive, first_checksums, _ = rel.release('0.2.0')
    second_archive, second_checksums, _ = rel.release('0.3.0')
    ready, release = threading.Event(), threading.Event()
    errors = []
    original = Path.replace
    def replace(path, target):
        if threading.current_thread().name == 'synthetic-first-update':
            ready.set()
            if not release.wait(5): raise TimeoutError('synthetic update synchronization')
        return original(path, target)
    def first():
        try: installer.install(first_archive, first_checksums, rel.install_root, rel.bin, rel.dirs)
        except BaseException as exc: errors.append(exc)
    worker = threading.Thread(target=first, name='synthetic-first-update')
    second_error = None
    with patch.object(Path, 'replace', replace):
        worker.start()
        try:
            assert ready.wait(5)
            try: installer.install(second_archive, second_checksums, rel.install_root, rel.bin, rel.dirs)
            except ValueError as exc: second_error = exc
        finally:
            release.set(); worker.join(5)
    assert not worker.is_alive(); assert errors == []
    state = json.loads((rel.install_root/'installation.json').read_text())
    assert (rel.bin/'ahl').resolve().parent.name == state['version']
    assert state['version'] == '0.2.0'
    assert isinstance(second_error, ValueError)
    assert 'in progress' in str(second_error)


def test_checksum_failure_has_no_install_side_effects(rel):
    archive, checksums, _ = rel.release()
    archive.write_bytes(archive.read_bytes()+b'changed')
    with pytest.raises(ValueError, match='checksum'):
        installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs)
    assert not rel.install_root.exists()


@pytest.mark.parametrize('hard', [pytest.param(False, id='symlink'), pytest.param(True, id='hardlink')])
def test_linked_installation_lock_is_refused_without_changing_links(rel, hard):
    archive, checksums, _ = rel.release()
    rel.install_root.mkdir()
    outside = rel.root/'unrelated-lock'; outside.write_text('synthetic-original')
    lock = rel.install_root/'.install.lock'
    if hard: lock.hardlink_to(outside)
    else: lock.symlink_to(outside)
    try:
        with pytest.raises((ValueError, OSError)):
            installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs)
        assert not rel.bin.exists()
        assert not (rel.install_root/'installation.json').exists()
        assert outside.read_text() == 'synthetic-original'
    finally: lock.unlink()


def test_foreign_owner_lock_is_refused(rel):
    archive, checksums, _ = rel.release()
    original = os.fstat
    def foreign(descriptor):
        values = list(original(descriptor)); values[4] = os.geteuid()+1
        return os.stat_result(values)
    with patch.object(os, 'fstat', foreign), pytest.raises(ValueError, match='owned regular'):
        installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs)
    assert not rel.bin.exists()


def test_killed_installer_releases_same_lock_inode_for_explicit_recovery(rel):
    if 'fork' not in multiprocessing.get_all_start_methods(): pytest.skip('POSIX fork required')
    archive, checksums, _ = rel.release()
    installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs)
    archive, checksums, _ = rel.release('0.2.0')
    context = multiprocessing.get_context('fork'); ready = context.Event(); release = context.Event()
    def paused():
        def wait_for_parent(*args):
            ready.set(); release.wait(15)
        with patch.object(Path, 'replace', wait_for_parent):
            installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs)
    worker = context.Process(target=paused); worker.start()
    try:
        assert ready.wait(5)
        lock = rel.install_root/'.install.lock'; inode = lock.stat().st_ino
        with pytest.raises(ValueError, match='in progress'):
            installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs)
        worker.kill(); worker.join(5); assert not worker.is_alive()
        installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs)
        assert lock.stat().st_ino == inode
        assert (rel.bin/'ahl').resolve().parent.name == '0.2.0'
        assert json.loads((rel.install_root/'installation.json').read_text())['version'] == '0.2.0'
    finally:
        if worker.is_alive(): worker.terminate(); worker.join(5)


def test_foreign_link_rejected_before_any_link_mutation(rel):
    rel.dirs[1].mkdir(parents=True)
    foreign = rel.dirs[1] / installer.SKILLS[1]
    foreign.symlink_to(rel.root/'unrelated')
    archive, checksums, _ = rel.release()
    with pytest.raises(ValueError, match='foreign symlink'):
        installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs)
    assert not rel.bin.exists()
    assert foreign.readlink() == rel.root/'unrelated'


def test_partial_link_failure_restores_existing_content(rel):
    original = rel.dirs[0] / installer.SKILLS[0]
    original.mkdir(parents=True)
    (original/'SKILL.md').write_text('original')
    archive, checksums, _ = rel.release()
    real = Path.symlink_to
    def fail_last(path, target, **kwargs):
        if path == rel.dirs[1]/installer.SKILLS[1]:
            raise OSError('synthetic failure')
        return real(path, target, **kwargs)
    with patch.object(Path, 'symlink_to', fail_last), pytest.raises(OSError):
        installer.install(archive, checksums, rel.install_root, rel.bin, rel.dirs)
    assert not (rel.bin/'ahl').exists()
    assert (original/'SKILL.md').read_text() == 'original'
    assert not (rel.install_root/'releases/0.1.0').exists()


def test_tar_traversal_and_symlinks_rejected(rel):
    for name, kind in [('../outside', tarfile.REGTYPE), ('root/link', tarfile.SYMTYPE)]:
        archive = rel.root / 'unsafe.tar.gz'
        with tarfile.open(archive, 'w:gz') as stream:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = 1 if kind == tarfile.REGTYPE else 0
            member.linkname = '/outside' if kind == tarfile.SYMTYPE else ''
            stream.addfile(member, io.BytesIO(b'x') if member.size else None)
        with pytest.raises(ValueError):
            installer.extract(archive, rel.root/'extract')
        assert not (rel.root/'outside').exists()


def test_build_excludes_private_and_generated_files(rel):
    (rel.source/'.ai-memory.toml').write_text('PRIVATE')
    (rel.source/'artifacts').mkdir()
    (rel.source/'artifacts/private.txt').write_text('PRIVATE')
    (rel.source/'skills/harness-evaluation/test_private.py').write_text('PRIVATE')
    archive, _, _ = rel.release()
    with tarfile.open(archive) as stream:
        names = stream.getnames()
        bodies = b''.join(stream.extractfile(m).read() for m in stream.getmembers() if m.isfile())
    assert not any('artifacts' in n or '.ai-memory' in n or 'test_private' in n for n in names)
    assert b'PRIVATE' not in bodies


def test_untracked_application_code_and_docs_are_excluded(rel):
    (rel.source/'tools/private.py').write_text('PRIVATE')
    (rel.source/'skills/private.md').write_text('PRIVATE')
    (rel.source/'agent_harness_lab/private.py').write_text('PRIVATE')
    archive, _, _ = rel.release()
    with tarfile.open(archive) as stream:
        assert not any('private' in n for n in stream.getnames())
        assert b'PRIVATE' not in b''.join(stream.extractfile(m).read() for m in stream if m.isfile())


def test_publication_requires_clean_commit_and_reviewed_app_files(rel):
    subprocess.run(['git', '-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid',
                    'commit', '--quiet', '-m', 'synthetic fixture'], cwd=rel.source, check=True)
    publisher.assert_clean(rel.source)
    (rel.source/'tools/private.py').write_text('PRIVATE')
    with pytest.raises(ValueError, match='untracked application'):
        publisher.assert_clean(rel.source)
    (rel.source/'tools/private.py').unlink()
    (rel.source/'README.md').write_text('changed')
    with pytest.raises(ValueError, match='clean committed'):
        publisher.assert_clean(rel.source)


def test_header_count_limit_stops_before_extracting(rel):
    archive = rel.root/'headers.tar.gz'
    with tarfile.open(archive, 'w:gz') as stream:
        for index in range(1002):
            member = tarfile.TarInfo(f'root/file-{index}')
            member.size = 0
            stream.addfile(member, io.BytesIO(b''))
    destination = rel.root/'extracted'
    with patch.object(tarfile.TarFile, 'getmembers', side_effect=AssertionError('must not materialize all headers')):
        with pytest.raises(ValueError, match='archive size'):
            installer.extract(archive, destination)
    assert not destination.exists()


def test_version_sync_updates_python_and_lock(rel):
    (rel.source/'package-lock.json').write_text('{"version":"0.1.0","packages":{"":{"version":"0.1.0"}}}')
    (rel.source/'package.json').write_text('{"version":"0.2.0"}')
    assert syncer.sync(rel.source) == '0.2.0'
    assert 'version = "0.2.0"' in (rel.source/'pyproject.toml').read_text()
    assert json.loads((rel.source/'package-lock.json').read_text())['packages']['']['version'] == '0.2.0'
