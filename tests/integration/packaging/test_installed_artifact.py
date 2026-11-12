"""Acceptance of the INSTALLED artifact (#685): the reported scenarios run through the installed launcher.

By default the archive is built from this checkout with scripts/build_release.py. With AHL_RELEASE_DIR set (see
`task release:verify`) the downloaded published archive is installed instead, so a release is verified end to end.
Offline, with a temporary HOME and state directory; the real home and state directory are never used.
"""
import os
from pathlib import Path
import re
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


def test_update_replaces_only_the_region_and_rollback_restores_the_previous_bytes(installed, tmp_path):
    """Install the archive, update to a newer one with the installed release's own installer, then undo it."""
    first = scenarios.install(installed.release / 'install.py', installed.archive, installed.checksums, tmp_path)
    claude = first.home / '.claude/CLAUDE.md'
    before = {path: path.read_bytes() for path in (claude, first.home / '.codex/AGENTS.md')}
    newer, sums = scenarios.reversioned(installed.archive, '999.0.0', tmp_path / 'next')
    second = scenarios.install(first.release / 'install.py', newer, sums, tmp_path, update=True)
    after = claude.read_bytes()
    assert b'version=999.0.0 sha256=' in after and b'version=' + installed.version.encode() not in after
    assert scenarios.REGION.sub(b'', after) == scenarios.HAND_WRITTEN == scenarios.REGION.sub(b'', before[claude])
    assert second.release.name == '999.0.0' and 'Undo this installation with: ahl rollback install-' in second.output
    name = re.search(r'ahl rollback (install-\S+)', second.output)[1]
    env = {'PATH': str(Path(sys.executable).parent), 'HOME': str(first.home)}
    done = subprocess.run([str(second.launcher), 'rollback', name], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert {path: path.read_bytes() for path in before} == before
    assert (tmp_path / 'bin/ahl').resolve().parent.name == installed.version


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
