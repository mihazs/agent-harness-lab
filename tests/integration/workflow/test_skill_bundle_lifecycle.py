"""Synthetic project skill bundles: create, update and remove are validated as whole bundles, then journaled."""
import shutil
from unittest.mock import patch

import pytest

from agent_harness_lab.promotion.journal import Journal
from agent_harness_lab.workflow import adopt, candidate_states, snapshot
from agent_harness_lab.sources import skill_validation

ENTRY = '---\nname: {name}\ndescription: Summarize release notes\n---\nRead [the guide](references/guide.md).\n'
ALPHA = '.claude/skills/alpha'


def write(base, name, text):
    path = base / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    write(root, 'AGENTS.md', 'rules\n')
    write(root, f'{ALPHA}/SKILL.md', ENTRY.format(name='alpha'))
    write(root, f'{ALPHA}/references/guide.md', 'guide\n')
    state = patch('agent_harness_lab.workflow.state_root', return_value=tmp_path / 'state')
    ignored = patch('agent_harness_lab.promotion.journal.ignored', return_value=False)
    with state, ignored:
        yield root


class Stage:
    def __init__(self, project):
        self.project, self.root = project, project.parent
        self.original = snapshot(project, self.root / 'baseline')
        shutil.copytree(self.root / 'baseline', self.root / 'candidate')
        self.candidate = self.root / 'candidate'

    def write(self, name, text): write(self.candidate, name, text)

    def remove(self, name): (self.candidate / name).unlink()

    def review(self):
        self.proposed = candidate_states(self.candidate, self.original)
        return skill_validation.review_tree(self.root / 'baseline', self.candidate, self.original, self.proposed)

    def adopt(self):
        self.review()
        return adopt(self.project, self.root / 'baseline', self.candidate, self.original, self.proposed)


def reasons(review):
    return {row.bundle: (row.action, row.reasons) for row in review.findings}


def test_create_update_and_remove_are_distinct_operations(project):
    stage = Stage(project)
    stage.write('.claude/skills/beta/SKILL.md', ENTRY.format(name='beta'))
    stage.write('.claude/skills/beta/references/guide.md', 'guide\n')
    stage.write(f'{ALPHA}/references/more.md', 'more\n')
    stage.write(f'{ALPHA}/SKILL.md', ENTRY.format(name='alpha') + 'Also [more](references/more.md).\n')
    stage.write('.agents/skills/gamma/SKILL.md', ENTRY.format(name='gamma').replace('Read [the guide](references/guide.md).', ''))
    assert reasons(stage.review()) == {'.claude/skills/beta': ('create', ()), ALPHA: ('update', ()),
                                       '.agents/skills/gamma': ('create', ())}


def test_complete_removal_adopts_and_rolls_back_through_the_journal(project):
    stage = Stage(project)
    shutil.rmtree(stage.candidate / ALPHA)
    assert reasons(stage.review()) == {ALPHA: ('remove', ())}
    assert stage.adopt() == [f'{ALPHA}/SKILL.md', f'{ALPHA}/references/guide.md']
    assert not (project / ALPHA).exists() or not any((project / ALPHA).rglob('*.md'))
    journal = Journal(project, next((project.parent / 'state/promotions').iterdir()))
    journal.rollback(journal.active)
    assert (project / ALPHA / 'references/guide.md').read_text() == 'guide\n'
    assert (project / ALPHA / 'SKILL.md').read_text() == ENTRY.format(name='alpha')


def test_valid_update_and_create_adopt(project):
    stage = Stage(project)
    stage.write(f'{ALPHA}/references/guide.md', 'revised\n')
    stage.write('.claude/skills/beta/SKILL.md', ENTRY.format(name='beta').replace('references/guide.md', 'notes.md'))
    stage.write('.claude/skills/beta/notes.md', 'notes\n')
    assert stage.adopt() == [f'{ALPHA}/references/guide.md', '.claude/skills/beta/SKILL.md', '.claude/skills/beta/notes.md']
    assert (project / '.claude/skills/beta/notes.md').read_text() == 'notes\n'


@pytest.mark.parametrize('edit, expected', [
    (lambda s: s.remove(f'{ALPHA}/SKILL.md'), 'missing_entry_point'),
    (lambda s: s.write(f'{ALPHA}/SKILL.md', 'no frontmatter\n'), 'missing_frontmatter'),
    (lambda s: s.write(f'{ALPHA}/SKILL.md', '---\nname: alpha\nname: again\n---\n'), 'bad_frontmatter'),
    (lambda s: s.write(f'{ALPHA}/SKILL.md', '---\nname: alpha\n---\nRead [g](references/guide.md).\n'), 'missing_description'),
    (lambda s: s.write(f'{ALPHA}/SKILL.md', ENTRY.format(name='Other')), 'invalid_name'),
    (lambda s: s.write(f'{ALPHA}/SKILL.md', ENTRY.format(name='alpha') + '[x](references/gone.md)\n'), 'dangling_reference'),
    (lambda s: s.write(f'{ALPHA}/SKILL.md', ENTRY.format(name='alpha') + '[x](../beta/SKILL.md)\n'), 'reference_escapes_bundle'),
    (lambda s: s.write(f'{ALPHA}/references/extra.md', 'unmentioned\n'), 'orphan_asset'),
    (lambda s: s.remove(f'{ALPHA}/references/guide.md'), 'dangling_reference'),
])
def test_invalid_bundle_is_blocked_and_never_applied(project, edit, expected):
    stage = Stage(project)
    edit(stage)
    review = stage.review()
    assert review.verdict == 'blocked' and expected in review.findings[0].reasons
    assert review.outcome()['reason'] == 'skill_bundle_validation_failed'
    with pytest.raises(ValueError, match='skill bundle is blocked'):
        adopt(project, stage.root / 'baseline', stage.candidate, stage.original, stage.proposed)
    assert (project / ALPHA / 'SKILL.md').read_text() == ENTRY.format(name='alpha')
    assert (project / ALPHA / 'references/guide.md').exists()


def test_new_namespace_without_entry_point_is_an_orphan(project):
    stage = Stage(project)
    stage.write('.claude/skills/orphan/references/x.md', 'x\n')
    assert reasons(stage.review()) == {'.claude/skills/orphan': ('create', ('missing_entry_point',))}


def test_unchanged_bundles_are_not_reviewed(project):
    stage = Stage(project)
    stage.write('AGENTS.md', 'new rules\n')
    assert stage.review().findings == ()


def test_stale_project_edit_rejects_removal_without_overwriting_user_work(project):
    stage = Stage(project)
    shutil.rmtree(stage.candidate / ALPHA)
    stage.review()
    write(project, f'{ALPHA}/references/guide.md', 'user edit\n')
    with pytest.raises(ValueError, match='changed before adoption'):
        adopt(project, stage.root / 'baseline', stage.candidate, stage.original, stage.proposed)
    assert (project / ALPHA / 'references/guide.md').read_text() == 'user edit\n'


def test_codex_skill_root_requires_a_name(project):
    stage = Stage(project)
    stage.write('.agents/skills/delta/SKILL.md', '---\ndescription: Summarize\n---\n')
    assert reasons(stage.review()) == {'.agents/skills/delta': ('create', ('missing_name',))}
