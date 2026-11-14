import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

spec = importlib.util.spec_from_file_location('link_skills', Path(__file__).resolve().parents[3] / 'tools/link_skills.py')
links = importlib.util.module_from_spec(spec)
spec.loader.exec_module(links)


class SkillTrees:
    """Source and destination skill directories, each holding every owned skill."""

    def __init__(self, root):
        self.root = root
        self.source, self.dest, self.backups = [self.root / p for p in ('source', 'dest', 'backups')]
        for name in links.NAMES:
            (self.source / name).mkdir(parents=True)
            (self.source / name / 'SKILL.md').write_text('new')
            (self.dest / name).mkdir(parents=True)
            (self.dest / name / 'SKILL.md').write_text('old')


@pytest.fixture
def trees(tmp_path):
    return SkillTrees(tmp_path)


def test_links_keep_originals_and_rerun_is_noop(trees):
    backup = links.install(trees.source, trees.dest, trees.backups)
    for name in links.NAMES:
        assert (backup / name / 'SKILL.md').read_text() == 'old'
        assert (trees.dest / name).resolve() == trees.source / name
    assert links.install(trees.source, trees.dest, trees.backups) is None


def test_claude_cli_installs_the_skill_without_touching_codex(trees):
    with patch.object(Path, 'home', return_value=trees.root), patch('builtins.print'):
        links.main(['--agent', 'claude'])
    for name in links.NAMES:
        link = trees.root / '.claude/skills' / name
        assert link.is_symlink()
        assert (link / 'SKILL.md').is_file()
    assert not (trees.root / '.agents').exists()


def test_foreign_link_rejected_before_any_mutation(trees):
    name = links.NAMES[-1]
    (trees.dest / name / 'SKILL.md').unlink()
    (trees.dest / name).rmdir()
    (trees.dest / name).symlink_to(trees.root / 'elsewhere')
    with pytest.raises(ValueError):
        links.install(trees.source, trees.dest, trees.backups)
    assert (trees.dest / name).readlink() == trees.root / 'elsewhere'
    assert not trees.backups.exists()


def test_partial_install_failure_restores_the_directory(trees):
    original = Path.symlink_to
    def fail_second(path, *args, **kwargs):
        if path.name == links.NAMES[-1]:
            raise OSError('synthetic link failure')
        return original(path, *args, **kwargs)
    with patch.object(Path, 'symlink_to', fail_second), pytest.raises(OSError):
        links.install(trees.source, trees.dest, trees.backups)
    for name in links.NAMES:
        assert not (trees.dest / name).is_symlink()
        assert (trees.dest / name / 'SKILL.md').read_text() == 'old'


RETIRED = links.RETIRED[0]


def stale_link(trees, target):
    """A two-skill install left this link behind; the retired skill no longer exists in the source."""
    link = trees.dest / RETIRED
    link.symlink_to(target, target_is_directory=True)
    return link


def test_upgrade_from_two_skill_install_removes_the_stale_source_link(trees):
    link = stale_link(trees, trees.source / RETIRED)
    backup = links.install(trees.source, trees.dest, trees.backups)
    assert not link.is_symlink() and not link.exists()
    assert (backup / RETIRED).is_symlink() and (backup / RETIRED).readlink() == trees.source / RETIRED
    assert (trees.dest / links.NAMES[0]).resolve() == trees.source / links.NAMES[0]
    assert links.install(trees.source, trees.dest, trees.backups) is None


def test_stale_link_alone_is_removed_even_when_the_current_link_is_in_place(trees):
    links.install(trees.source, trees.dest, trees.backups)
    link = stale_link(trees, trees.source / RETIRED)
    assert links.install(trees.source, trees.dest, trees.backups) is not None
    assert not link.is_symlink()


def test_stale_link_into_an_installer_release_is_removed(trees):
    release = trees.root / 'data/releases/0.1.0'
    (release / 'skills' / RETIRED).mkdir(parents=True)
    (release / '.release-sha256').write_text('synthetic\n')
    link = stale_link(trees, release / 'skills' / RETIRED)
    links.install(trees.source, trees.dest, trees.backups)
    assert not link.is_symlink()
    assert (release / 'skills' / RETIRED).is_dir()


def test_foreign_or_ordinary_retired_entries_are_left_alone(trees):
    other = trees.root / 'other/skills' / RETIRED
    other.mkdir(parents=True)
    link = stale_link(trees, other)
    links.install(trees.source, trees.dest, trees.backups)
    assert link.readlink() == other
    link.unlink()
    (trees.dest / RETIRED).mkdir()
    (trees.dest / RETIRED / 'SKILL.md').write_text('mine')
    links.install(trees.source, trees.dest, trees.backups)
    assert (trees.dest / RETIRED / 'SKILL.md').read_text() == 'mine'
