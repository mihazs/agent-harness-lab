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


def test_claude_cli_installs_both_skills_without_touching_codex(trees):
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
    assert (trees.dest / links.NAMES[0] / 'SKILL.md').read_text() == 'old'
    assert not trees.backups.exists()


def test_partial_install_failure_restores_both_directories(trees):
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
