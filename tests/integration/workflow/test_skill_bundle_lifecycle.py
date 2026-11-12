"""Synthetic project skill bundles: create, update and remove are validated as whole bundles, then journaled."""
import shutil
import time
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


@pytest.mark.parametrize('link', ['references/', 'references/my%20guide.md', '<references/my guide.md>',
                                  'references/../SKILL.md', './references/guide.md', 'references/x/../guide.md'])
def test_valid_link_forms_are_not_blocked(project, link):
    write(project, f'{ALPHA}/references/my guide.md', 'spaced\n')
    stage = Stage(project)
    stage.write(f'{ALPHA}/SKILL.md', ENTRY.format(name='alpha') + f'[x]({link})\n[m](references/my%20guide.md)\n')
    assert reasons(stage.review()) == {ALPHA: ('update', ())}


@pytest.mark.parametrize('link, expected', [('references/nothing/', 'dangling_reference'),
                                            ('references/../../beta', 'reference_escapes_bundle')])
def test_invalid_link_forms_stay_blocked(project, link, expected):
    stage = Stage(project)
    stage.write(f'{ALPHA}/SKILL.md', ENTRY.format(name='alpha') + f'[x]({link})\n')
    assert expected in stage.review().findings[0].reasons


def test_many_unclosed_fences_are_reviewed_in_linear_time(project):
    stage = Stage(project)
    stage.write(f'{ALPHA}/SKILL.md', ENTRY.format(name='alpha') + '```a\n' * 20000)
    started = time.monotonic()
    assert reasons(stage.review()) == {ALPHA: ('update', ())}
    assert time.monotonic() - started < 2


def test_many_links_over_many_files_are_reviewed_in_linear_time(project):
    stage = Stage(project)
    for index in range(300):
        stage.write(f'{ALPHA}/references/f{index}.md', 'x')
    stage.write(f'{ALPHA}/SKILL.md', ENTRY.format(name='alpha') + '[x](gone)\n[y](references)\n' * 100000)
    started = time.monotonic()
    assert reasons(stage.review()) == {ALPHA: ('update', ('dangling_reference', 'orphan_asset'))}
    assert time.monotonic() - started < 2


def test_fence_closer_needs_the_opener_length_and_no_info_string(project):
    stage = Stage(project)
    hidden = '````\n```\n[x](references/gone.md)\n```js\n````\n'
    stage.write(f'{ALPHA}/SKILL.md', ENTRY.format(name='alpha') + hidden)
    assert reasons(stage.review()) == {ALPHA: ('update', ())}
    stage.write(f'{ALPHA}/SKILL.md', ENTRY.format(name='alpha') + '```\ncode\n```\n[x](references/gone.md)\n')
    assert reasons(stage.review()) == {ALPHA: ('update', ('dangling_reference',))}


def scoped_stage(project, paths):
    stage = Stage(project)
    stage.original = snapshot(project, project.parent / 'scoped', allowed_paths=paths)
    shutil.rmtree(stage.root / 'candidate')
    shutil.copytree(project.parent / 'scoped', stage.root / 'candidate')
    return stage


def scoped_review(stage):
    proposed = candidate_states(stage.candidate, stage.original)
    return skill_validation.review_tree(stage.root / 'scoped', stage.candidate, stage.original, proposed, scoped=True)


def test_partial_bundle_scope_does_not_block_an_asset_edit(project):
    stage = scoped_stage(project, [f'{ALPHA}/references/guide.md'])
    stage.write(f'{ALPHA}/references/guide.md', 'revised\n')
    assert scoped_review(stage).verdict == 'adoptable'


def test_partial_bundle_scope_does_not_block_an_entry_point_edit(project):
    stage = scoped_stage(project, [f'{ALPHA}/SKILL.md'])
    stage.write(f'{ALPHA}/SKILL.md', ENTRY.format(name='alpha').replace('Summarize', 'Digest'))
    assert scoped_review(stage).verdict == 'adoptable'


def test_scoped_review_still_judges_the_listed_entry_point(project):
    stage = scoped_stage(project, [f'{ALPHA}/SKILL.md'])
    stage.write(f'{ALPHA}/SKILL.md', 'no frontmatter\n')
    assert scoped_review(stage).findings[0].reasons == ('missing_frontmatter',)


@pytest.mark.parametrize('path,blocked', [
    ('pkg/.claude/skills/alpha', True), ('.agents/skills/alpha', False), ('pkg/.claude/skills/beta', False)])
def test_new_bundle_shadowing_an_existing_name_is_blocked(project, path, blocked):
    stage = Stage(project)
    stage.write(f'{path}/SKILL.md', ENTRY.format(name=path.rsplit('/', 1)[1]).replace('Read [the guide](references/guide.md).', ''))
    assert reasons(stage.review())[path] == ('create', ('shadowed_name',) if blocked else ())


def test_shadowing_is_judged_against_the_candidate_and_skips_updates(project):
    stage = Stage(project)
    stage.write('pkg/.claude/skills/alpha/SKILL.md', ENTRY.format(name='alpha'))
    stage.write('pkg/.claude/skills/alpha/references/guide.md', 'guide\n')
    stage.write(f'{ALPHA}/references/more.md', 'more\n')
    assert reasons(stage.review())[ALPHA] == ('update', ('orphan_asset',))
    stage.remove(f'{ALPHA}/SKILL.md'); stage.remove(f'{ALPHA}/references/guide.md'); stage.remove(f'{ALPHA}/references/more.md')
    assert reasons(stage.review())['pkg/.claude/skills/alpha'] == ('create', ())
