"""A complete skill bundle removal is blocked while a remaining file still names the removed bundle; synthetic only."""
import shutil

import pytest

from agent_harness_lab.sources import skill_validation
from agent_harness_lab.workflow import adopt
from tests.integration.workflow.test_skill_bundle_lifecycle import (  # noqa: F401
    ALPHA, ENTRY, Stage, project as project, reasons, scoped_review, scoped_stage, write)

BETA = '.claude/skills/beta'
BLOCKED = {ALPHA: ('remove', ('removed_bundle_referenced',))}


def beta_entry(extra=''):
    return ENTRY.format(name='beta').replace('Read [the guide](references/guide.md).', '') + extra


def removal(project, files=None):
    """The project with the extra files written, then a candidate that deletes the whole alpha bundle."""
    write(project, f'{BETA}/SKILL.md', beta_entry())
    for name, text in (files or {}).items():
        write(project, name, text)
    stage = Stage(project)
    shutil.rmtree(stage.candidate / ALPHA)
    return stage


@pytest.mark.parametrize('files', [
    pytest.param({'AGENTS.md': 'Run [alpha](.claude/skills/alpha/SKILL.md).\n'}, id='instruction-markdown-link'),
    pytest.param({'AGENTS.md': 'Run [alpha][a].\n\n[a]: .claude/skills/alpha/references/guide.md\n'}, id='instruction-reference-link'),
    pytest.param({'CLAUDE.md': 'Use .claude/skills/alpha when summarizing.\n'}, id='instruction-path-mention'),
    pytest.param({'AGENTS.md': 'See ./.claude/skills/alpha/\n'}, id='instruction-dot-slash-folder-mention'),
    pytest.param({'AGENTS.md': '```\npython .claude/skills/alpha/run.py\n```\n'}, id='instruction-fenced-path-mention'),
    pytest.param({f'{BETA}/SKILL.md': beta_entry('Also [alpha](../alpha/SKILL.md).\n')}, id='sibling-bundle-link'),
    pytest.param({f'{BETA}/references/notes.md': 'Read ../../alpha/references/guide.md first.\n'}, id='sibling-asset-relative-mention'),
    pytest.param({f'{BETA}/notes.txt': 'See .claude/skills/alpha/SKILL.md\n'}, id='sibling-asset-project-path-mention'),
    pytest.param({f'{BETA}/run.py': 'ROOT = ".claude/skills/alpha/references"\n'}, id='sibling-script-path-mention'),
])
def test_removal_is_blocked_while_a_remaining_file_names_the_bundle(project, files):
    stage = removal(project, files)
    review = stage.review()
    assert reasons(review) == BLOCKED
    assert review.outcome()['reason'] == 'skill_bundle_validation_failed'


def test_blocked_removal_is_refused_by_adoption_and_leaves_the_project_untouched(project):
    stage = removal(project, {'AGENTS.md': 'Run [alpha](.claude/skills/alpha/SKILL.md).\n'})
    stage.review()
    with pytest.raises(ValueError, match='skill bundle is blocked'):
        adopt(project, stage.root / 'baseline', stage.candidate, stage.original, stage.proposed)
    assert (project / ALPHA / 'SKILL.md').read_text() == ENTRY.format(name='alpha')
    assert (project / ALPHA / 'references/guide.md').read_text() == 'guide\n'


@pytest.mark.parametrize('files', [
    pytest.param({'AGENTS.md': 'Use .claude/skills/alpha-extended/SKILL.md\n'}, id='longer-folder-name'),
    pytest.param({'AGENTS.md': 'Read .claude/skills/alpha.md\n'}, id='file-name-sharing-the-prefix'),
    pytest.param({'AGENTS.md': 'Read ~/.claude/skills/alpha/SKILL.md\n'}, id='user-level-folder'),
    pytest.param({'AGENTS.md': 'https://example.invalid/.claude/skills/alpha/SKILL.md\n'}, id='url-path'),
    pytest.param({'AGENTS.md': 'Read .agents/skills/alpha/SKILL.md\n'}, id='other-kind-of-folder'),
    pytest.param({'AGENTS.md': 'Use the alpha skill for release notes.\n'}, id='name-only-prose'),
    pytest.param({'AGENTS.md': '[alpha](https://example.invalid/alpha)\n'}, id='external-link'),
    pytest.param({'AGENTS.md': '`[a](.claude/skills/other/SKILL.md)`\n'}, id='link-to-another-bundle'),
    pytest.param({'AGENTS.md': ''}, id='empty-instruction-file'),
    pytest.param({f'{BETA}/SKILL.md': beta_entry('Read [it](references/own.md).\n'), f'{BETA}/references/own.md': 'own\n'},
                 id='link-inside-its-own-bundle'),
])
def test_removal_is_not_blocked_by_text_that_does_not_name_the_bundle(project, files):
    assert reasons(removal(project, files).review()) == {ALPHA: ('remove', ())}


def test_a_bundle_naming_itself_does_not_block_its_own_removal(project):
    write(project, f'{ALPHA}/references/guide.md', 'See .claude/skills/alpha/SKILL.md\n')
    assert reasons(removal(project).review()) == {ALPHA: ('remove', ())}


def test_removal_passes_once_the_same_candidate_drops_the_reference(project):
    stage = removal(project, {'AGENTS.md': 'Run [alpha](.claude/skills/alpha/SKILL.md).\n'})
    stage.write('AGENTS.md', 'rules\n')
    assert reasons(stage.review()) == {ALPHA: ('remove', ())}
    assert stage.adopt() == [f'{ALPHA}/SKILL.md', f'{ALPHA}/references/guide.md', 'AGENTS.md']


def test_two_bundles_removed_together_do_not_block_each_other(project):
    write(project, f'{ALPHA}/references/guide.md', 'See .claude/skills/beta/SKILL.md\n')
    stage = removal(project, {f'{BETA}/references/notes.md': 'See .claude/skills/alpha/SKILL.md\n'})
    shutil.rmtree(stage.candidate / BETA)
    assert reasons(stage.review()) == {ALPHA: ('remove', ()), BETA: ('remove', ())}


def test_consolidation_into_a_sibling_passes_when_the_sibling_drops_its_reference(project):
    stage = removal(project, {f'{BETA}/SKILL.md': beta_entry('Also [alpha](../alpha/SKILL.md).\n')})
    stage.write(f'{BETA}/references/alpha-guide.md', 'guide\n')
    stage.write(f'{BETA}/SKILL.md', beta_entry('Also [the merged guide](references/alpha-guide.md).\n'))
    assert reasons(stage.review()) == {ALPHA: ('remove', ()), BETA: ('update', ())}


def test_updating_a_bundle_is_not_a_removal_even_when_others_name_it(project):
    write(project, 'AGENTS.md', 'Run [alpha](.claude/skills/alpha/SKILL.md).\n')
    stage = Stage(project)
    stage.write(f'{ALPHA}/references/guide.md', 'revised\n')
    assert reasons(stage.review()) == {ALPHA: ('update', ())}


def test_scoped_snapshot_cannot_judge_references_and_claims_no_removal(project):
    write(project, 'AGENTS.md', 'Run [alpha](.claude/skills/alpha/SKILL.md).\n')
    stage = scoped_stage(project, ['AGENTS.md', f'{ALPHA}/SKILL.md'])
    (stage.candidate / ALPHA / 'SKILL.md').unlink()
    assert [(row.action, row.reasons) for row in scoped_review(stage).findings] == [('scoped', ())]


def test_a_remaining_file_changed_after_hashing_stops_the_review(project):
    stage = removal(project, {'AGENTS.md': 'rules\n'})
    stage.review()
    write(stage.candidate, 'AGENTS.md', 'changed after the states were hashed\n')
    with pytest.raises(ValueError, match='changed during skill bundle review'):
        skill_validation.review_tree(stage.root / 'baseline', stage.candidate, stage.original, stage.proposed)
