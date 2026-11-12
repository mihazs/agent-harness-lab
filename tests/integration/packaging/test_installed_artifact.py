"""Acceptance of the INSTALLED artifact (#685): the reported scenarios run through the installed launcher.

By default the archive is built from this checkout with scripts/build_release.py. With AHL_RELEASE_DIR set (see
`task release:verify`) the downloaded published archive is installed instead, so a release is verified end to end.
Offline, with a temporary HOME and state directory; the real home and state directory are never used.
"""
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from agent_harness_lab.cli import version
from tests.integration.packaging import installed_scenarios as scenarios

ROOT = Path(__file__).resolve().parents[3]
PUBLISHED = os.environ.get('AHL_RELEASE_DIR')


@pytest.fixture(scope='module')
def installed(tmp_path_factory):
    root = tmp_path_factory.mktemp('installed')
    if PUBLISHED:
        folder = Path(PUBLISHED)
        archive, checksums, installer = next(folder.glob('agent-harness-lab-*.tar.gz')), folder / 'SHA256SUMS', folder / 'install.py'
    else:
        built = tmp_path_factory.mktemp('built')
        subprocess.run([sys.executable, str(ROOT / 'scripts/build_release.py'), '--output', str(built)],
                       cwd=ROOT, check=True, capture_output=True, timeout=120)
        archive, checksums, installer = next(built.glob('*.tar.gz')), built / 'SHA256SUMS', built / 'install.py'
    return scenarios.install(installer, archive, checksums, root)


@pytest.mark.parametrize('scenario', scenarios.SCENARIOS, ids=lambda f: f.__name__.removeprefix('scenario_'))
def test_reported_scenario_passes_through_the_installed_launcher(scenario, installed, tmp_path):
    scenario(installed, scenarios.Sandbox(installed, tmp_path))


def test_installed_launcher_reports_the_expected_version(installed):
    assert installed.version == version()
    out = subprocess.run([str(installed.launcher), '--version'], capture_output=True, text=True, timeout=30,
                         env={'PATH': str(Path(sys.executable).parent), 'HOME': str(installed.home)})
    assert out.stdout.strip() == installed.version


def test_launcher_and_skill_links_resolve_inside_the_same_release_directory(installed):
    assert len(installed.skill_links) == 4  # two skills in each of the codex and claude global directories
    assert installed.launcher.resolve().parent == installed.release
    for link in installed.skill_links:
        assert link.is_symlink() and link.resolve().is_relative_to(installed.release / 'skills'), link


def test_installed_skill_text_equals_the_archive_skill(installed):
    with tarfile.open(installed.archive) as archive:
        packed = {m.name.split('/', 1)[1]: archive.extractfile(m).read() for m in archive if m.isfile()
                  and m.name.split('/', 1)[1].startswith('skills/') and m.name.endswith('SKILL.md')}
    assert len(packed) == 2
    for name, content in packed.items():
        assert (installed.release / name).read_bytes() == content
        for link in installed.skill_links:
            if link.name == Path(name).parent.name:
                assert (link / 'SKILL.md').read_bytes() == content
