"""Synthetic staged reference-style skill links are checked without executing assets."""
import pytest

from tests.integration.workflow.test_skill_bundle_lifecycle import ALPHA, ENTRY, Stage
from tests.integration.workflow.test_skill_bundle_lifecycle import project as project


@pytest.mark.parametrize(('definition','reason'),[
    ('[guide]: references/missing.md','dangling_reference'),
    ('[guide]: ../other/secret.md','reference_escapes_bundle'),
    ('[guide]: %2e%2e%2fother/secret.md','reference_escapes_bundle'),
])
def test_used_reference_definition_blocks_missing_or_escaping_target(project,definition,reason):
    stage = Stage(project)
    stage.write(f'{ALPHA}/SKILL.md',ENTRY.format(name='alpha')+'Read [the reference][guide].\n'+definition+'\n')
    assert reason in stage.review().findings[0].reasons
    with pytest.raises(ValueError,match='skill bundle is blocked'): stage.adopt()


@pytest.mark.parametrize('extra',[
    'Read [the reference][guide].\n[guide]: references/guide.md\n',
    'Read [guide][].\n[guide]: <references/guide.md>\n',
    'Read [the reference][guide].\n[guide]: https://example.invalid/path\n',
    '```markdown\n[the reference][guide]\n[guide]: references/missing.md\n```\n',
    '<!--\n[the reference][guide]\n[guide]: references/missing.md\n-->\n',
    '[unused]: references/missing.md\n',
])
def test_valid_external_or_nonrendered_reference_is_not_blocked(project,extra):
    stage = Stage(project); stage.write(f'{ALPHA}/SKILL.md',ENTRY.format(name='alpha')+extra)
    assert stage.review().verdict == 'adoptable'


def test_reference_labels_are_case_and_whitespace_insensitive_with_first_definition(project):
    stage = Stage(project)
    stage.write(f'{ALPHA}/SKILL.md',ENTRY.format(name='alpha')+
        'Read [this][A  Guide].\n[a guide]: references/missing.md "title"\n[A GUIDE]: references/guide.md\n')
    assert 'dangling_reference' in stage.review().findings[0].reasons


def test_unreferenced_or_escaped_reference_does_not_create_a_link(project):
    stage = Stage(project)
    stage.write(f'{ALPHA}/SKILL.md',ENTRY.format(name='alpha')+
        r'An escaped \[label][guide] is literal.'+'\n[guide]: references/missing.md\n')
    assert stage.review().verdict == 'adoptable'
