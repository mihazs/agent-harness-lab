"""The staged candidate copy of a read-only harness file accepts the proposer's edit and keeps the original modes (#807)."""
import os
import stat

import pytest

from agent_harness_lab.workflow import writable_candidate

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason='a superuser writes through file permissions')


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def staged(tmp_path):
    folder = tmp_path / 'candidate'
    (folder / 'skills/review').mkdir(parents=True)
    for name, bits in (('AGENTS.md', 0o444), ('skills/review/SKILL.md', 0o400), ('CLAUDE.md', 0o644), ('skills/review/run.sh', 0o555)):
        (folder / name).write_text('synthetic\n')
        (folder / name).chmod(bits)
    yield folder
    for path in (folder / 'skills/review', folder / 'skills', folder):
        path.chmod(0o755)


def test_a_read_only_file_can_be_edited_inside_and_gets_its_mode_back_after(staged):
    with writable_candidate(staged):
        (staged / 'AGENTS.md').write_text('edited\n')
    assert (staged / 'AGENTS.md').read_text() == 'edited\n' and mode(staged / 'AGENTS.md') == 0o444


def test_a_read_only_directory_takes_a_new_file_inside_and_gets_its_mode_back_after(staged):
    (staged / 'skills/review').chmod(0o555)
    with writable_candidate(staged):
        (staged / 'skills/review/NOTES.md').write_text('new\n')
    assert (staged / 'skills/review/NOTES.md').read_text() == 'new\n' and mode(staged / 'skills/review') == 0o555


def test_files_that_were_already_writable_are_never_touched(staged):
    with writable_candidate(staged):
        assert mode(staged / 'CLAUDE.md') == 0o644
    assert mode(staged / 'CLAUDE.md') == 0o644


@pytest.mark.parametrize('name, before, during', [('AGENTS.md', 0o444, 0o644), ('skills/review/SKILL.md', 0o400, 0o600), ('skills/review/run.sh', 0o555, 0o755)],
                         ids=['group-readable', 'owner-only', 'executable'])
def test_each_read_only_mode_is_opened_for_its_owner_only_and_restored(staged, name, before, during):
    with writable_candidate(staged):
        assert mode(staged / name) == during
    assert mode(staged / name) == before


def test_a_mode_the_proposer_changed_itself_is_kept(staged):
    with writable_candidate(staged):
        (staged / 'AGENTS.md').chmod(0o755)
    assert mode(staged / 'AGENTS.md') == 0o755


def test_modes_are_restored_when_the_proposer_raises(staged):
    with pytest.raises(KeyboardInterrupt):
        with writable_candidate(staged):
            raise KeyboardInterrupt
    assert mode(staged / 'AGENTS.md') == 0o444


def test_a_file_the_proposer_removed_does_not_stop_the_restore(staged):
    with writable_candidate(staged):
        (staged / 'AGENTS.md').unlink()
    assert not (staged / 'AGENTS.md').exists() and mode(staged / 'skills/review/SKILL.md') == 0o400


def test_an_empty_folder_is_left_alone(tmp_path):
    folder = tmp_path / 'candidate'
    folder.mkdir()
    with writable_candidate(folder):
        pass
    assert mode(folder) == 0o755
