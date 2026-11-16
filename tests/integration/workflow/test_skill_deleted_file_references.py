"""An update that deletes a file of a skill bundle is blocked while a remaining file still names that file; synthetic only."""
import pytest

from agent_harness_lab.sources import skill_validation
from agent_harness_lab.workflow import adopt
from tests.integration.workflow.test_skill_bundle_lifecycle import (  # noqa: F401
    ALPHA, ENTRY, Stage, project as project, reasons, scoped_review, scoped_stage, write)

GUIDE = f'{ALPHA}/references/guide.md'
BLOCKED = {ALPHA: ('update', ('removed_file_referenced',))}
UNLINKED = ENTRY.format(name='alpha').replace('Read [the guide](references/guide.md).', 'Summarize.')
PASSING = {ALPHA: ('update', ())}


def beta_entry(extra=''):
    return ENTRY.format(name='beta').replace('Read [the guide](references/guide.md).', '') + extra


def deletion(project, files=None, entry=UNLINKED, delete=(GUIDE,)):
    """The project with the extra files written, then a candidate whose alpha SKILL.md no longer links the deleted files."""
    for name, text in (files or {}).items():
        write(project, name, text)
    stage = Stage(project)
    for name in delete:
        stage.remove(name)
    stage.write(f'{ALPHA}/SKILL.md', entry)
    return stage


@pytest.mark.parametrize('files, entry', [
    pytest.param({'AGENTS.md': 'Read [the guide](.claude/skills/alpha/references/guide.md).\n'}, UNLINKED, id='instruction-markdown-link'),
    pytest.param({'AGENTS.md': 'Read [the guide][g].\n\n[g]: .claude/skills/alpha/references/guide.md\n'}, UNLINKED, id='instruction-reference-link'),
    pytest.param({'CLAUDE.md': 'Open .claude/skills/alpha/references/guide.md first.\n'}, UNLINKED, id='instruction-path-mention'),
    pytest.param({'AGENTS.md': '```\ncat ./.claude/skills/alpha/references/guide.md\n```\n'}, UNLINKED, id='instruction-fenced-path-mention'),
    pytest.param({'.claude/skills/beta/SKILL.md': beta_entry('Also [guide](../alpha/references/guide.md).\n')}, UNLINKED,
                 id='sibling-bundle-link'),
    pytest.param({'.claude/skills/beta/SKILL.md': beta_entry(), '.claude/skills/beta/notes.txt': 'See ../alpha/references/guide.md\n'},
                 UNLINKED, id='sibling-bundle-relative-mention'),
    pytest.param({f'{ALPHA}/references/notes.md': 'Start from guide.md.\n'}, UNLINKED, id='own-asset-relative-mention'),
    pytest.param({f'{ALPHA}/references/index.md': 'See [guide](guide.md).\n'}, UNLINKED, id='own-asset-link'),
    pytest.param({}, UNLINKED + 'Read references/guide.md.\n', id='own-entry-point-path-mention'),
])
def test_update_is_blocked_while_a_remaining_file_names_a_deleted_file(project, files, entry):
    stage = deletion(project, files, entry)
    review = stage.review()
    assert reasons(review)[ALPHA][0] == 'update' and 'removed_file_referenced' in reasons(review)[ALPHA][1]
    assert review.outcome()['reason'] == 'skill_bundle_validation_failed'


def test_only_the_named_file_blocks_when_several_files_are_deleted(project):
    write(project, f'{ALPHA}/references/more.md', 'more\n')
    stage = deletion(project, {'AGENTS.md': 'Read .claude/skills/alpha/references/more.md\n'},
                     delete=(GUIDE, f'{ALPHA}/references/more.md'))
    assert reasons(stage.review()) == BLOCKED


def test_blocked_update_is_refused_by_adoption_and_leaves_the_project_untouched(project):
    stage = deletion(project, {'AGENTS.md': 'Read [the guide](.claude/skills/alpha/references/guide.md).\n'})
    stage.review()
    with pytest.raises(ValueError, match='skill bundle is blocked'):
        adopt(project, stage.root / 'baseline', stage.candidate, stage.original, stage.proposed)
    assert (project / ALPHA / 'SKILL.md').read_text() == ENTRY.format(name='alpha')
    assert (project / GUIDE).read_text() == 'guide\n'


@pytest.mark.parametrize('files', [
    pytest.param({'AGENTS.md': 'Read .claude/skills/alpha/references/guide-v2.md\n'}, id='longer-file-name'),
    pytest.param({'AGENTS.md': 'Read .claude/skills/alpha/references/guide.md.bak\n'}, id='file-name-with-another-extension'),
    pytest.param({'AGENTS.md': 'Read ~/.claude/skills/alpha/references/guide.md\n'}, id='user-level-folder'),
    pytest.param({'AGENTS.md': 'https://example.invalid/.claude/skills/alpha/references/guide.md\n'}, id='url-path'),
    pytest.param({'AGENTS.md': 'Read .agents/skills/alpha/references/guide.md\n'}, id='other-kind-of-folder'),
    pytest.param({'AGENTS.md': 'Read references/guide.md for the guide.\n'}, id='path-relative-to-another-folder'),
    pytest.param({'AGENTS.md': 'Read the guide.md notes.\n'}, id='bare-file-name-in-prose'),
    pytest.param({'AGENTS.md': '[guide](https://example.invalid/references/guide.md)\n'}, id='external-link'),
    pytest.param({'AGENTS.md': '`[a](.claude/skills/other/references/guide.md)`\n'}, id='link-to-another-bundle'),
    pytest.param({'AGENTS.md': ''}, id='empty-instruction-file'),
])
def test_update_is_not_blocked_by_text_that_does_not_name_the_deleted_file(project, files):
    assert reasons(deletion(project, files).review()) == PASSING


def test_update_passes_when_the_deleted_file_is_replaced_under_a_new_name(project):
    stage = deletion(project, entry=ENTRY.format(name='alpha').replace('references/guide.md', 'references/guide-v2.md'))
    stage.write(f'{ALPHA}/references/guide-v2.md', 'guide\n')
    assert reasons(stage.review()) == PASSING


def test_update_passes_once_the_same_candidate_drops_the_reference(project):
    stage = deletion(project, {'AGENTS.md': 'Read [the guide](.claude/skills/alpha/references/guide.md).\n'})
    stage.write('AGENTS.md', 'rules\n')
    assert reasons(stage.review()) == PASSING
    assert stage.adopt() == [f'{ALPHA}/SKILL.md', GUIDE, 'AGENTS.md']


def test_update_that_keeps_every_file_is_not_blocked_even_when_others_name_one(project):
    write(project, 'AGENTS.md', 'Read [the guide](.claude/skills/alpha/references/guide.md).\n')
    stage = Stage(project)
    stage.write(GUIDE, 'revised\n')
    assert reasons(stage.review()) == PASSING


def test_a_file_moved_to_another_bundle_still_blocks_a_reference_to_the_old_path(project):
    write(project, 'AGENTS.md', 'Read [the guide](.claude/skills/alpha/references/guide.md).\n')
    stage = deletion(project, delete=(GUIDE,))
    stage.write('.claude/skills/beta/SKILL.md', beta_entry('Read [the guide](notes/guide.md).\n'))
    stage.write('.claude/skills/beta/notes/guide.md', 'guide\n')
    assert reasons(stage.review()) == {ALPHA: BLOCKED[ALPHA], '.claude/skills/beta': ('create', ())}


def test_scoped_snapshot_cannot_judge_references_to_a_deleted_file(project):
    write(project, 'AGENTS.md', 'Read [the guide](.claude/skills/alpha/references/guide.md).\n')
    stage = scoped_stage(project, ['AGENTS.md', f'{ALPHA}/SKILL.md', GUIDE])
    (stage.candidate / GUIDE).unlink()
    (stage.candidate / ALPHA / 'SKILL.md').write_text(UNLINKED)
    assert [(row.action, row.reasons) for row in scoped_review(stage).findings] == [('scoped', ())]


def test_a_remaining_file_changed_after_hashing_stops_the_review(project):
    stage = deletion(project, {'AGENTS.md': 'rules\n'})
    stage.review()
    write(stage.candidate, 'AGENTS.md', 'changed after the states were hashed\n')
    with pytest.raises(ValueError, match='changed during skill bundle review'):
        skill_validation.review_tree(stage.root / 'baseline', stage.candidate, stage.original, stage.proposed)
