"""The installer's archive member bound must hold for the archive this checkout ships, with room to grow."""
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('release_installer_bound', ROOT / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture(scope='module')
def archive(tmp_path_factory):
    built = tmp_path_factory.mktemp('built')
    subprocess.run([sys.executable, str(ROOT / 'scripts/build_release.py'), '--output', str(built)], cwd=ROOT, check=True, capture_output=True, timeout=120)
    return next(built.glob('*.tar.gz'))


def test_installer_extracts_the_archive_this_checkout_builds(archive, tmp_path):
    assert installer.extract(archive, tmp_path).is_dir()


def test_shipped_archive_stays_within_three_quarters_of_the_installer_member_bound(archive):
    with tarfile.open(archive) as stream:
        assert len(stream.getnames()) <= installer.MAX_MEMBERS * 3 // 4


def tiny_archive(path, count):
    with tarfile.open(path, 'w:gz') as stream:
        for number in range(count):
            stream.addfile(tarfile.TarInfo(f'release/f{number}'), io.BytesIO(b''))
    return path


def test_installer_accepts_an_archive_with_exactly_the_bound_of_members(tmp_path):
    assert installer.extract(tiny_archive(tmp_path / 'full.tar.gz', installer.MAX_MEMBERS), tmp_path / 'out').is_dir()


def test_installer_refuses_an_archive_one_member_over_the_bound(tmp_path):
    with pytest.raises(ValueError, match='invalid release archive size'):
        installer.extract(tiny_archive(tmp_path / 'over.tar.gz', installer.MAX_MEMBERS + 1), tmp_path / 'out')
